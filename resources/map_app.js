/* Карта: MapLibre + deck.gl. Связь с Python:
     JS -> Python:  console.log("PY:" + JSON.stringify({type: ...}))
     Python -> JS:  window.api.<метод>(...)  (runJavaScript)
   Состояние хранится в state, поэтому вызовы api до загрузки карты не теряются. */
(function () {
  'use strict';
  const U = window.GeoUtils;
  const send = (o) => console.log('PY:' + JSON.stringify(o));

  const STYLES = {
    light: 'https://tiles.openfreemap.org/styles/positron',
    dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    detailed: 'https://tiles.openfreemap.org/styles/liberty',
  };
  const ZONES = [   // порядок отрисовки: от большей зоны к меньшей; col — столбец в rays
    { name: 'weak', col: 4, fill: 'rgba(231,100,50,0.45)', stroke: 'rgba(231,100,50,0.9)' },
    { name: 'good', col: 3, fill: 'rgba(241,196,15,0.55)', stroke: 'rgba(241,196,15,0.95)' },
    { name: 'excellent', col: 2, fill: 'rgba(163,217,69,0.60)', stroke: 'rgba(163,217,69,0.95)' },
    { name: 'green', col: 1, fill: 'rgba(46,204,113,0.70)', stroke: 'rgba(46,204,113,1.0)' },
  ];
  const HINTS = {
    navigate: '', select: 'Режим выбора — кликните на вышку',
    place: 'Режим размещения — кликните на карту',
    measure: 'Линейка: ЛКМ — точка · ПКМ/Backspace — отменить · Esc — выйти',
    probe: 'Замер сигнала — кликните в любую точку',
    zone_algo: 'Участок алгоритма: ЛКМ — вершины, двойной клик/Enter — завершить, ПКМ — отменить',
    zone_forbidden: 'Запретная зона: ЛКМ — вершины, двойной клик/Enter — завершить, ПКМ — отменить',
    device: 'Установка устройства — кликните на карту (тип выбран в палитре «Сеть»)',
    cable: 'Кабель: кликните первое устройство, затем второе (Esc — отмена)',
    pdu: 'PDU (ping): кликните устройство-источник, затем получателя',
  };
  const ROLE_COLORS = { tower: '#ff5d5d', switch_l2: '#3e8bff', switch_l3: '#7c5cff', router: '#2ec4b6',
    firewall: '#ff9f1c', radio: '#ff4d9d', core: '#30c26f' };
  const ROLE_ICONS = {
    switch_l2: '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/>',
    switch_l3: '<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><line x1="7" y1="7" x2="7.01" y2="7"/><line x1="7" y1="17" x2="7.01" y2="17"/><line x1="12" y1="10" x2="12" y2="14"/>',
    router: '<circle cx="12" cy="12" r="9"/><polyline points="9 9 12 6 15 9"/><polyline points="9 15 12 18 15 15"/><polyline points="9 9 6 12 9 15"/><polyline points="15 9 18 12 15 15"/>',
    firewall: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    radio: '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49"/>',
    core: '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
    tower: '<circle cx="12" cy="12" r="2"/>',
  };
  const EMPTY = { type: 'FeatureCollection', features: [] };

  const state = {
    mode: 'navigate', towers: [], selectedId: null, theme: 'dark', base: 'dark',
    zones: { algo: null, forbidden: [] }, measure: [], draft: [], rectShape: false,
    probe: null, ready: false, azPreview: null,
    net: { devices: [], links: [] }, selDevice: null, selLink: null, cableFrom: null, traffic: null,
    mouse: null,
  };
  let map = null, deckOverlay = null;
  const towerMarkers = new Map();
  let measureMarkers = [], probeMarker = null, azMarker = null, azDragging = false, azTimer = null;
  const devMarkers = new Map(), linkLabels = new Map();
  let armMarker = null, animReq = 0, animTimer = null, animMarkers = [], flowReq = 0;

  const $ = (id) => document.getElementById(id);

  function fail(msg) { const e = $('map-error'); if (e) { e.textContent = msg; e.style.display = 'block'; } }

  /* ---------------- построение данных ---------------- */
  function rotated(t) {                     // предпросмотр поворота: те же дальности, другие углы
    const p = state.azPreview;
    if (!p || p.id !== t.id) return t;
    const d = p.azimuth - t.azimuth;
    return Object.assign({}, t, { azimuth: p.azimuth,
      rays: t.rays.map((r) => [((r[0] + d) % 360 + 360) % 360, r[1], r[2], r[3], r[4]]) });
  }

  function coverageFeatures() {
    const feats = [];
    for (const t0 of state.towers) {
      const t = rotated(t0);
      for (const z of ZONES) {
        const ring = U.zoneRing(t, z.col);
        if (ring) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] },
          properties: { fill: z.fill, stroke: z.stroke, sel: t.id === state.selectedId ? 1 : 0 } });
      }
    }
    return { type: 'FeatureCollection', features: feats };
  }
  const toRing = (poly) => { const r = poly.map((p) => [p[1], p[0]]); r.push(r[0].slice()); return r; };

  function zoneFeatures() {
    const feats = [];
    if (state.zones.algo && state.zones.algo.length >= 3)
      feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [toRing(state.zones.algo)] },
        properties: { kind: 'algo' } });
    for (const f of state.zones.forbidden)
      if (f.length >= 3) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [toRing(f)] },
        properties: { kind: 'forbidden' } });
    return { type: 'FeatureCollection', features: feats };
  }
  function lineAndPoints(pts, closed) {
    const c = pts.map((p) => [p[1], p[0]]);
    const feats = c.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: {} }));
    if (c.length >= 2) feats.push({ type: 'Feature', properties: {},
      geometry: { type: 'LineString', coordinates: closed && c.length >= 3 ? c.concat([c[0]]) : c } });
    return { type: 'FeatureCollection', features: feats };
  }

  /* ---------------- отрисовка ---------------- */
  function setData(id, data) { const s = map && map.getSource(id); if (s) s.setData(data); }

  function renderCoverage() { setData('coverage', coverageFeatures()); renderDeck(); renderTowerMarkers(); renderAzimuthHandle(); }
  function renderZones() { setData('zones', zoneFeatures()); }
  function renderDraft() { setData('draft', lineAndPoints(state.draft, true)); }

  function renderMeasure() {
    setData('measure', lineAndPoints(state.measure, false));
    measureMarkers.forEach((m) => m.remove());
    measureMarkers = [];
    if (!map) return;
    let total = 0;
    for (let i = 1; i < state.measure.length; i++) {
      const a = state.measure[i - 1], b = state.measure[i];
      const d = U.haversine(a[0], a[1], b[0], b[1]);
      total += d;
      const el = document.createElement('div');
      el.className = 'lbl';
      el.textContent = U.fmtDist(d) + ' · ' + U.fmtBearing(U.bearing(a[0], a[1], b[0], b[1]));
      measureMarkers.push(new maplibregl.Marker({ element: el })
        .setLngLat([(a[1] + b[1]) / 2, (a[0] + b[0]) / 2]).addTo(map));
    }
    if (state.measure.length >= 2) {
      const last = state.measure[state.measure.length - 1];
      const el = document.createElement('div');
      el.className = 'lbl total';
      el.textContent = 'Σ ' + U.fmtDist(total);
      measureMarkers.push(new maplibregl.Marker({ element: el, offset: [0, -22] })
        .setLngLat([last[1], last[0]]).addTo(map));
    }
  }

  function renderTowerMarkers() {
    if (!map) return;
    const ids = new Set(state.towers.map((t) => t.id));
    for (const [id, m] of towerMarkers) if (!ids.has(id)) { m.remove(); towerMarkers.delete(id); }
    for (const t of state.towers) {
      let m = towerMarkers.get(t.id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'tower-pin';
        el.innerHTML = '<span class="dot"></span><span class="name"></span>';
        m = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([t.lon, t.lat]).addTo(map);
        towerMarkers.set(t.id, m);
      }
      m.setLngLat([t.lon, t.lat]);
      const el = m.getElement();
      el.querySelector('.name').textContent = t.name;
      el.querySelector('.dot').style.background = 'rgb(' + t.color.slice(0, 3).join(',') + ')';
      el.classList.toggle('selected', t.id === state.selectedId);
    }
  }

  /* ---------------- ручка азимута (только для секторных вышек) ---------------- */
  function lineFrom(a, b) {
    return { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {},
      geometry: { type: 'LineString', coordinates: [a, b] } }] };
  }
  function handleLngLat(t, az) {            // ~80 px от вышки по азимуту, при любом масштабе
    const cosL = Math.cos(t.lat * Math.PI / 180);
    const dist = Math.max(15, 80 * 78271.517 * cosL / Math.pow(2, map.getZoom()));
    const a = az * Math.PI / 180;
    return [t.lon + Math.sin(a) * dist / (111320 * cosL), t.lat + Math.cos(a) * dist / 111320];
  }
  const selectedTower = () => state.towers.find((x) => x.id === state.selectedId);

  function renderAzimuthHandle() {
    if (!map) return;
    const t = selectedTower();
    const show = t && t.beamwidth < 360 && state.mode === 'navigate';
    if (!show) {
      if (azMarker) { azMarker.remove(); azMarker = null; }
      setData('azline', EMPTY);
      return;
    }
    if (azDragging) return;
    const az = state.azPreview && state.azPreview.id === t.id ? state.azPreview.azimuth : t.azimuth;
    const pos = handleLngLat(t, az);
    if (!azMarker) {
      const el = document.createElement('div');
      el.className = 'az-handle';
      el.innerHTML = '<span class="knob"></span><span class="az-text"></span>';
      azMarker = new maplibregl.Marker({ element: el, draggable: true }).setLngLat(pos).addTo(map);
      azMarker.on('drag', onAzDrag);
      azMarker.on('dragend', onAzEnd);
    } else azMarker.setLngLat(pos);
    azMarker.getElement().querySelector('.az-text').textContent = Math.round(az) + '°';
    setData('azline', lineFrom([t.lon, t.lat], pos));
  }
  function onAzDrag() {
    const t = selectedTower();
    if (!t || !azMarker) return;
    azDragging = true;
    const ll = azMarker.getLngLat();
    const az = Math.round(U.bearing(t.lat, t.lon, ll.lat, ll.lng)) % 360;
    state.azPreview = { id: t.id, azimuth: az };
    setData('coverage', coverageFeatures());
    setData('azline', lineFrom([t.lon, t.lat], [ll.lng, ll.lat]));
    azMarker.getElement().querySelector('.az-text').textContent = az + '°';
  }
  function onAzEnd() {
    const t = selectedTower();
    azDragging = false;
    if (!t || !state.azPreview) return;
    send({ type: 'azimuth', id: t.id, azimuth: state.azPreview.azimuth });
    clearTimeout(azTimer);                  // если Python не ответил — вернём прежний вид
    azTimer = setTimeout(() => { if (!azDragging && state.azPreview) { state.azPreview = null; renderCoverage(); } }, 3000);
  }


  /* ---------------- сеть: устройства, кабели, трафик ---------------- */
  const utilColor = (u) => 'hsl(' + Math.round(120 * (1 - Math.min(Math.max(u, 0), 1))) + ',85%,48%)';
  const linkUtil = (id) => (state.traffic && state.traffic.linkUtil && state.traffic.linkUtil[id] !== undefined)
    ? state.traffic.linkUtil[id] : null;

  function linkFeatures() {
    return { type: 'FeatureCollection', features: state.net.links.map((l) => {
      const u = linkUtil(l.id);
      const col = (u !== null && l.state !== 'down') ? utilColor(u) : (l.state === 'warn' ? '#f5a524' : l.color);
      return { type: 'Feature', properties: { id: l.id, state: l.state, col, width: l.width,
        sel: l.id === state.selLink ? 1 : 0 },
        geometry: { type: 'LineString', coordinates: [[l.a[1], l.a[0]], [l.b[1], l.b[0]]] } };
    }) };
  }

  function renderNet() {
    setData('netlinks', linkFeatures());
    if (!map) return;
    const ids = new Set(state.net.devices.filter((d) => !d.tower).map((d) => d.id));
    for (const [id, m] of devMarkers) if (!ids.has(id)) { m.remove(); devMarkers.delete(id); }
    for (const d of state.net.devices) {
      if (d.tower) continue;
      let m = devMarkers.get(d.id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'net-dev';
        el.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round">' + (ROLE_ICONS[d.role] || '') + '</svg><span class="nm"></span>';
        m = new maplibregl.Marker({ element: el, draggable: true }).setLngLat([d.lon, d.lat]).addTo(map);
        m.on('dragend', () => { const ll = m.getLngLat(); send({ type: 'net_move', id: d.id, lat: ll.lat, lon: ll.lng }); });
        devMarkers.set(d.id, m);
      }
      m.setLngLat([d.lon, d.lat]);
      const el = m.getElement();
      el.style.setProperty('--c', ROLE_COLORS[d.role] || '#888');
      el.querySelector('.nm').textContent = d.name;
      el.classList.toggle('selected', d.id === state.selDevice);
      el.classList.toggle('unpowered', d.powered === false);
    }
    const lids = new Set(state.net.links.map((l) => l.id));
    for (const [id, m] of linkLabels) if (!lids.has(id)) { m.remove(); linkLabels.delete(id); }
    for (const l of state.net.links) {
      let m = linkLabels.get(l.id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'link-lbl';
        m = new maplibregl.Marker({ element: el }).addTo(map);
        linkLabels.set(l.id, m);
      }
      m.setLngLat([(l.a[1] + l.b[1]) / 2, (l.a[0] + l.b[0]) / 2]);
      const el = m.getElement();
      const u = linkUtil(l.id);
      el.textContent = l.label + (u !== null ? ' · ' + Math.round(u * 100) + '%' : '');
      el.classList.toggle('bad', l.state === 'down');
      el.classList.toggle('warn', l.state === 'warn');
      el.classList.toggle('selected', l.id === state.selLink);
    }
    updateLinkLabelVisibility();
    renderFlowDots(performance.now());
    renderArm();
  }

  function updateLinkLabelVisibility() {
    if (!map) return;
    const show = map.getZoom() >= 14.5;
    for (const [id, m] of linkLabels) m.getElement().style.display = (show || id === state.selLink) ? '' : 'none';
  }

  function renderArm() {
    const d = state.cableFrom && state.net.devices.find((x) => x.id === state.cableFrom);
    if (!d) { if (armMarker) { armMarker.remove(); armMarker = null; } return; }
    if (!armMarker) {
      const el = document.createElement('div');
      el.className = 'arm-ring';
      armMarker = new maplibregl.Marker({ element: el }).addTo(map);
    }
    armMarker.setLngLat([d.lon, d.lat]);
  }

  function pickDevice(point) {
    let best = null, bestD = 26;
    for (const d of state.net.devices) {
      const p = map.project([d.lon, d.lat]);
      const dist = Math.hypot(p.x - point.x, p.y - point.y);
      if (dist < bestD) { bestD = dist; best = d; }
    }
    return best;
  }
  function segDist(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
    const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function pickLink(point) {
    let best = null, bestD = 9;
    for (const l of state.net.links) {
      const d = segDist(point, map.project([l.a[1], l.a[0]]), map.project([l.b[1], l.b[0]]));
      if (d < bestD) { bestD = d; best = l; }
    }
    return best;
  }

  function renderTraffic() {
    const t = state.traffic;
    const users = t ? t.users.map((u) => ({ type: 'Feature', properties: { st: u[2] },
      geometry: { type: 'Point', coordinates: [u[1], u[0]] } })) : [];
    const lines = t ? t.lines.map((l) => ({ type: 'Feature', properties: { st: l[4] },
      geometry: { type: 'LineString', coordinates: [[l[1], l[0]], [l[3], l[2]]] } })) : [];
    setData('trfusers', { type: 'FeatureCollection', features: users });
    setData('trflines', { type: 'FeatureCollection', features: lines });
    renderNet();
    if (t && !flowReq) flowReq = requestAnimationFrame(flowTick);
  }

  function renderFlowDots(now) {
    const feats = [];
    if (state.traffic && state.traffic.linkUtil) {
      for (const l of state.net.links) {
        const u = linkUtil(l.id);
        if (u === null || u <= 0.01 || l.state === 'down') continue;
        const n = 1 + Math.round(u * 5), period = 2600 - u * 1700;
        for (let k = 0; k < n; k++) {
          const f = ((now / period) + k / n) % 1;
          const g = 1 - (((now / period) * 0.8 + (k + 0.5) / n) % 1);
          feats.push({ type: 'Feature', properties: { col: utilColor(u) }, geometry: { type: 'Point',
            coordinates: [l.a[1] + (l.b[1] - l.a[1]) * f, l.a[0] + (l.b[0] - l.a[0]) * f] } });
          if (k % 2 === 0) feats.push({ type: 'Feature', properties: { col: utilColor(u) }, geometry: { type: 'Point',
            coordinates: [l.a[1] + (l.b[1] - l.a[1]) * g, l.a[0] + (l.b[0] - l.a[0]) * g] } });
        }
      }
    }
    setData('flow', { type: 'FeatureCollection', features: feats });
  }
  function flowTick(now) {
    flowReq = 0;
    if (!state.traffic) { renderFlowDots(now); return; }
    renderFlowDots(now);
    flowReq = requestAnimationFrame(flowTick);
  }

  function clearAnim() {
    if (animReq) { cancelAnimationFrame(animReq); animReq = 0; }
    clearTimeout(animTimer);
    animMarkers.forEach((m) => m.remove());
    animMarkers = [];
  }
  /* path — [[lat, lon], ...] через все узлы; пакет «едет» по ломаной, по окончании — результат ✓/✗ */
  function animate(path, ok, label) {
    clearAnim();
    if (!map || !path.length) return;
    const mk = (cls, text, ll) => {
      const el = document.createElement('div');
      el.className = cls;
      el.textContent = text;
      const m = new maplibregl.Marker({ element: el }).setLngLat([ll[1], ll[0]]).addTo(map);
      animMarkers.push(m);
      return m;
    };
    const finish = () => {
      const end = path[path.length - 1];
      mk('pkt-result ' + (ok ? 'ok' : 'fail'), (ok ? '✓ ' : '✗ ') + (label || ''), end);
      animTimer = setTimeout(clearAnim, 5000);
    };
    if (path.length < 2) { finish(); return; }
    const pkt = mk('pkt', '✉', path[0]);
    const segs = [];
    for (let i = 0; i < path.length - 1; i++) {
      const d = U.haversine(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1]);
      segs.push({ a: path[i], b: path[i + 1], dur: Math.max(350, Math.min(1100, d / 2)) });
    }
    let i = 0, t0 = performance.now();
    const step = (now) => {
      const sg = segs[i], k = Math.min(1, (now - t0) / sg.dur);
      pkt.setLngLat([sg.a[1] + (sg.b[1] - sg.a[1]) * k, sg.a[0] + (sg.b[0] - sg.a[0]) * k]);
      if (k >= 1) { i++; t0 = now; if (i >= segs.length) { pkt.remove(); finish(); return; } }
      animReq = requestAnimationFrame(step);
    };
    animReq = requestAnimationFrame(step);
  }

  function rubberBand() {
    const d = state.cableFrom && state.net.devices.find((x) => x.id === state.cableFrom);
    if (!d || !state.mouse) { renderDraft(); return; }
    setData('draft', lineFrom([d.lon, d.lat], [state.mouse.lng, state.mouse.lat]));
  }

  function renderDeck() {
    if (!deckOverlay || typeof deck === 'undefined') return;
    const layers = [];
    const plain = state.towers.filter((t) => !t.model_b64);
    const models = state.towers.filter((t) => t.model_b64);
    if (plain.length) layers.push(new deck.ColumnLayer({
      id: 'tower-columns-default', data: plain, getPosition: (d) => [d.lon, d.lat],
      getElevation: (d) => d.height, radius: 4, extruded: true, pickable: false,
      getFillColor: (d) => d.color,
    }));
    if (models.length) layers.push(new deck.ScenegraphLayer({
      id: 'tower-models-custom', data: models, pickable: false, scenegraph: (d) => d.model_b64,
      getPosition: (d) => [d.lon, d.lat], getOrientation: (d) => [0, 0, d.tilt],
      sizeScale: 1, getSize: (d) => d.scale,
    }));
    deckOverlay.setProps({ layers });
  }

  /* ---------------- слои карты (пересоздаются при смене подложки) ---------------- */
  function setupLayers() {
    if (map.getSource('coverage')) return;
    for (const id of ['coverage', 'zones', 'draft', 'measure', 'azline', 'netlinks', 'flow', 'trfusers', 'trflines']) map.addSource(id, { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'coverage-fill', type: 'fill', source: 'coverage',
      paint: { 'fill-color': ['get', 'fill'], 'fill-opacity': 0.75 } });
    map.addLayer({ id: 'coverage-outline', type: 'line', source: 'coverage',
      paint: { 'line-color': ['get', 'stroke'], 'line-opacity': 0.85,
        'line-width': ['case', ['==', ['get', 'sel'], 1], 3, 1.2] } });
    try {
      map.addSource('openfreemap-buildings', { type: 'vector', url: 'https://tiles.openfreemap.org/planet' });
      map.addLayer({ id: '3d-buildings-extrusion', source: 'openfreemap-buildings', 'source-layer': 'building',
        type: 'fill-extrusion', minzoom: 13, paint: {
          'fill-extrusion-color': state.theme === 'dark' ? '#4a5160' : '#c5ccd6',
          'fill-extrusion-height': ['*', ['coalesce', ['get', 'render_height'], ['get', 'height'], 15], 1.2],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], ['get', 'min_height'], 0],
          'fill-extrusion-opacity': 0.85 } });
    } catch (e) { console.warn('3D buildings unavailable: ' + e); }
    map.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', paint: {
      'fill-color': ['match', ['get', 'kind'], 'forbidden', '#e5484d', '#3e8bff'], 'fill-opacity': 0.18 } });
    map.addLayer({ id: 'zones-line', type: 'line', source: 'zones', paint: {
      'line-color': ['match', ['get', 'kind'], 'forbidden', '#e5484d', '#3e8bff'],
      'line-width': 2.5, 'line-dasharray': [2, 1.5] } });
    map.addLayer({ id: 'measure-line', type: 'line', source: 'measure', filter: ['==', '$type', 'LineString'],
      paint: { 'line-color': '#ffcc00', 'line-width': 3 } });
    map.addLayer({ id: 'measure-pts', type: 'circle', source: 'measure', filter: ['==', '$type', 'Point'],
      paint: { 'circle-radius': 5, 'circle-color': '#ffcc00', 'circle-stroke-color': '#000', 'circle-stroke-width': 1.5 } });
    map.addLayer({ id: 'draft-line', type: 'line', source: 'draft', filter: ['==', '$type', 'LineString'],
      paint: { 'line-color': '#ffffff', 'line-width': 2, 'line-dasharray': [1.5, 1.5] } });
    map.addLayer({ id: 'draft-pts', type: 'circle', source: 'draft', filter: ['==', '$type', 'Point'],
      paint: { 'circle-radius': 4.5, 'circle-color': '#ffffff', 'circle-stroke-color': '#3e8bff', 'circle-stroke-width': 2 } });
    map.addLayer({ id: 'azline', type: 'line', source: 'azline',
      paint: { 'line-color': '#3e8bff', 'line-width': 2.5, 'line-dasharray': [2, 1.5] } });
    map.addLayer({ id: 'trf-lines', type: 'line', source: 'trflines',
      paint: { 'line-color': ['match', ['get', 'st'], 2, 'rgba(245,165,36,0.45)', 'rgba(46,204,113,0.35)'], 'line-width': 1 } });
    map.addLayer({ id: 'trf-users', type: 'circle', source: 'trfusers', paint: {
      'circle-radius': ['match', ['get', 'st'], 0, 2, 3.5],
      'circle-color': ['match', ['get', 'st'], 1, '#2ecc71', 2, '#f5a524', 3, '#e5484d', '#8a93a0'],
      'circle-opacity': ['match', ['get', 'st'], 0, 0.45, 0.95], 'circle-stroke-width': 0.5,
      'circle-stroke-color': '#000' } });
    map.addLayer({ id: 'netlinks-down', type: 'line', source: 'netlinks', filter: ['==', ['get', 'state'], 'down'],
      paint: { 'line-color': '#e5484d', 'line-width': 3, 'line-dasharray': [1.5, 1.5] } });
    map.addLayer({ id: 'netlinks-solid', type: 'line', source: 'netlinks', filter: ['!=', ['get', 'state'], 'down'],
      paint: { 'line-color': ['get', 'col'],
        'line-width': ['+', ['get', 'width'], ['case', ['==', ['get', 'sel'], 1], 3, 0]] } });
    map.addLayer({ id: 'flow-dots', type: 'circle', source: 'flow', paint: {
      'circle-radius': 3.2, 'circle-color': '#ffffff', 'circle-stroke-color': ['get', 'col'], 'circle-stroke-width': 1.5 } });
    renderCoverage(); renderZones(); renderMeasure(); renderDraft(); renderNet(); renderTraffic();
  }

  /* ---------------- режимы и взаимодействие ---------------- */
  function drawing() { return state.mode === 'zone_algo' || state.mode === 'zone_forbidden'; }

  function setMapMode(mode) {
    if (drawing() && state.mode !== mode) { state.draft = []; renderDraft(); }
    state.mode = mode;
    state.cableFrom = null; renderArm(); renderDraft();
    const ind = $('mode-indicator');
    if (ind) { ind.textContent = HINTS[mode] || ''; ind.style.display = HINTS[mode] ? 'block' : 'none'; }
    if (map) {
      map.getCanvas().style.cursor = { select: 'pointer', place: 'cell', measure: 'crosshair', device: 'cell',
        cable: 'alias', pdu: 'alias', probe: 'crosshair', zone_algo: 'crosshair', zone_forbidden: 'crosshair' }[mode] || '';
      if (drawing()) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    }
    renderAzimuthHandle();
  }

  function pickTower(point) {
    let best = null, bestD = 28;
    for (const t of state.towers) {
      const p = map.project([t.lon, t.lat]);
      const d = Math.hypot(p.x - point.x, p.y - point.y);
      if (d < bestD) { bestD = d; best = t; }
    }
    return best;
  }

  function finishZone() {
    const kind = state.mode === 'zone_algo' ? 'algo' : 'forbidden';
    if (state.draft.length >= 3) send({ type: 'zone', kind, polygon: state.draft.slice() });
    state.draft = []; renderDraft();
  }
  function undoLast() {
    if (state.mode === 'measure') { state.measure.pop(); renderMeasure(); send({ type: 'measure', points: state.measure.slice() }); }
    else if (drawing()) { state.draft.pop(); renderDraft(); }
  }

  function onClick(e) {
    const lat = e.lngLat.lat, lon = e.lngLat.lng;
    switch (state.mode) {
      case 'navigate': case 'select': {
        const t = pickTower(e.point);
        if (t) { send({ type: 'select', id: t.id }); break; }
        const d = pickDevice(e.point);
        if (d) { state.selDevice = d.id; state.selLink = null; renderNet(); send({ type: 'net_select', id: d.id }); break; }
        const l = pickLink(e.point);
        if (l) { state.selLink = l.id; state.selDevice = null; renderNet(); renderCoverage(); send({ type: 'net_link_select', id: l.id }); break; }
        if (state.mode === 'select') send({ type: 'select', id: null });
        break;
      }
      case 'device': send({ type: 'net_place', lat, lon }); break;
      case 'cable': case 'pdu': {
        const d = pickDevice(e.point);
        if (!d) break;
        if (!state.cableFrom) { state.cableFrom = d.id; renderArm(); break; }
        if (d.id === state.cableFrom) break;
        const a = state.cableFrom;
        state.cableFrom = null; renderArm(); renderDraft();
        send({ type: state.mode === 'cable' ? 'net_connect' : 'net_pdu', a, b: d.id });
        break;
      }
      case 'place': send({ type: 'place', lat, lon }); break;
      case 'probe': send({ type: 'probe', lat, lon }); break;
      case 'measure':
        state.measure.push([lat, lon]); renderMeasure();
        send({ type: 'measure', points: state.measure.slice() });
        break;
      case 'zone_algo': case 'zone_forbidden':
        if (state.rectShape) {
          if (state.draft.length === 0) { state.draft = [[lat, lon]]; renderDraft(); break; }
          const a = state.draft[0];
          state.draft = [a, [a[0], lon], [lat, lon], [lat, a[1]]];
          finishZone();
        } else { state.draft.push([lat, lon]); renderDraft(); }
        break;
    }
  }

  function onDblClick() {
    if (!drawing() || state.rectShape) return;
    state.draft.pop();            // второй клик двойного клика добавил дубль вершины
    finishZone();
  }

  function onKey(ev) {
    if (ev.key === 'Escape') {
      state.draft = []; state.cableFrom = null; renderArm(); renderDraft(); clearAnim();
      send({ type: 'mode', mode: 'navigate' });
    } else if (ev.key === 'Backspace') undoLast();
    else if (ev.key === 'Enter' && drawing() && !state.rectShape) finishZone();
  }

  /* ---------------- публичный API для Python ---------------- */
  const api = {
    setTowers(list) { state.towers = list; state.azPreview = null; renderCoverage(); },
    selectTower(id) { state.selectedId = id; state.azPreview = null; renderCoverage(); },
    setZones(z) { state.zones = { algo: z.algo || null, forbidden: z.forbidden || [] }; renderZones(); },
    setMapMode,
    setRectShape(v) { state.rectShape = !!v; state.draft = []; renderDraft(); },
    clearMeasure() { state.measure = []; renderMeasure(); send({ type: 'measure', points: [] }); },
    setProbe(lat, lon, text, color) {
      if (!map) return;
      if (probeMarker) probeMarker.remove();
      const el = document.createElement('div');
      el.className = 'probe-pin';
      el.style.borderColor = color || '#fff';
      el.textContent = text || '';
      probeMarker = new maplibregl.Marker({ element: el, offset: [0, -16] }).setLngLat([lon, lat]).addTo(map);
    },
    clearProbe() { if (probeMarker) { probeMarker.remove(); probeMarker = null; } },
    flyTo(lat, lon, zoom) { if (map) map.flyTo({ center: [lon, lat], zoom: zoom || map.getZoom() }); },
    fitBounds(latMin, latMax, lonMin, lonMax) {
      if (map) map.fitBounds([[lonMin, latMin], [lonMax, latMax]], { padding: 60, duration: 600 });
    },
    setTheme(theme) {
      state.theme = theme;
      document.body.setAttribute('data-theme', theme);
      if (map && map.getLayer('3d-buildings-extrusion'))
        map.setPaintProperty('3d-buildings-extrusion', 'fill-extrusion-color', theme === 'dark' ? '#4a5160' : '#c5ccd6');
    },
    setBase(name) {
      if (!STYLES[name] || !map || state.base === name) return;
      state.base = name;
      map.setStyle(STYLES[name]);       // слои вернёт обработчик style.load
    },
    setNetwork(net) { state.net = net; renderNet(); },
    selectDevice(id) { state.selDevice = id; if (id) state.selLink = null; renderNet(); },
    selectLink(id) { state.selLink = id; if (id) state.selDevice = null; renderNet(); },
    animate,
    setTraffic(t) { state.traffic = t; renderTraffic(); },
    clearTraffic() { state.traffic = null; renderTraffic(); },
    getState() { return state; },
  };
  window.api = api;

  /* ---------------- запуск ---------------- */
  if (typeof maplibregl === 'undefined') {
    fail('Не удалось загрузить MapLibre (нет интернета?). Расчёты работают, карта недоступна.');
    return;
  }
  map = new maplibregl.Map({ container: 'map', style: STYLES[state.base], center: [28.8638, 47.0101],
    zoom: 16, pitch: 60, bearing: 45, antialias: true });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true, showZoom: true, showCompass: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

  map.on('style.load', () => {
    setupLayers();
    if (!deckOverlay && typeof deck !== 'undefined') {
      deckOverlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
      map.addControl(deckOverlay);
    }
    renderDeck();
    if (!state.ready) { state.ready = true; send({ type: 'ready' }); }
  });
  map.on('error', (e) => { if (e && e.error) console.warn('map error: ' + e.error.message); });
  map.on('zoom', () => { if (!azDragging) renderAzimuthHandle(); updateLinkLabelVisibility(); });
  map.on('click', onClick);
  map.on('dblclick', onDblClick);
  map.on('contextmenu', (e) => { e.preventDefault(); undoLast(); });

  let hoverThrottle = 0, hudThrottle = 0;
  map.on('mousemove', (e) => {
    const now = Date.now();
    state.mouse = e.lngLat;
    if (state.cableFrom) rubberBand();
    if (now - hudThrottle > 40) {
      hudThrottle = now;
      const h = $('hud-coords');
      if (h) h.textContent = e.lngLat.lat.toFixed(6) + ', ' + e.lngLat.lng.toFixed(6);
    }
    if (now - hoverThrottle > 1500) {
      hoverThrottle = now;
      send({ type: 'hover_load', lat: e.lngLat.lat, lon: e.lngLat.lng });
    }
  });
  document.addEventListener('keydown', onKey);
})();
