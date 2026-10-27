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
    cable: 'Кабель: кликните первое устройство или вышку, затем второе (ПКМ — отменить выбор, Esc — выход)',
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
    mouse: null, towerDrag: null,
    coverageMode: 'standard', selectedTowers: [], selectedDevices: [],
  };
  let map = null, deckOverlay = null;
  let measureMarkers = [], probeMarker = null, azMarker = null, azDragging = false, azTimer = null, dragTimer = null;
  const devMarkers = new Map();
  const rayCache = new Map();               // id вышки -> {rev, rays}: Python не пересылает неизменившиеся лучи
  const ringCache = new Map();              // id вышки -> {key, rings}: полигоны зон не пересчитываются зря
  let activeId = null;                      // вышка, которую сейчас крутят/тащат: рисуется в отдельном источнике
  let armMarker = null, animReq = 0, animTimer = null, animMarkers = [], flowReq = 0;
  let lastMousePoint = null, radialLngLat = null, boxSelecting = false, boxStart = null;

  const $ = (id) => document.getElementById(id);

  function fail(msg) { const e = $('map-error'); if (e) { e.textContent = msg; e.style.display = 'block'; } }

  /* ---------------- построение данных ---------------- */
  function previewOf(t0) {                  // предпросмотр поворота/переноса: те же дальности, другие углы/центр
    let t = t0;
    const p = state.azPreview;
    if (p && p.id === t.id) {
      const d = p.azimuth - t.azimuth;
      t = Object.assign({}, t, { azimuth: p.azimuth,
        rays: t.rays.map((r) => [((r[0] + d) % 360 + 360) % 360, r[1], r[2], r[3], r[4]]) });
    }
    const g = state.towerDrag;
    if (g && g.id === t.id && g.moved) t = Object.assign({}, t, { lat: g.lat, lon: g.lon });
    return t;
  }

  function towerRings(t0) {
    const t = previewOf(t0);
    const key = t.rev + '|' + t.azimuth + '|' + t.lat + '|' + t.lon;
    const c = ringCache.get(t.id);
    if (c && c.key === key) return c.rings;
    const rings = ZONES.map((z) => U.zoneRing(t, z.col));
    ringCache.set(t.id, { key, rings });
    return rings;
  }

  function isTowerSelected(id) {
    if (id === state.selectedId) return true;
    if (state.selectedTowers && state.selectedTowers.includes(id)) return true;
    return false;
  }

  function coverageFeatures(only) {         // only: undefined — все, кроме активной; id — только эта вышка
    const isBest = state.coverageMode === 'best_server';
    const feats = [];
    if (!isBest) {
      for (const t of state.towers) {
        if (only === undefined ? t.id === activeId : t.id !== only) continue;
        const rings = towerRings(t), sel = isTowerSelected(t.id) ? 1 : 0;
        ZONES.forEach((z, k) => {
          if (rings[k]) feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [rings[k]] },
            properties: { fill: z.fill, stroke: z.stroke, sel, pattern: '' } });
        });
      }
    } else {
      // Режим лучшей силы (штриховка):
      // Отрисовка от слабых зон к наилучшим (Weak -> Good -> Excellent -> Green).
      // Каждая более сильная зона перекрывает более слабую и оставляет только лучший уровень сигнала.
      for (let k = 0; k < ZONES.length; k++) {
        const z = ZONES[k];
        const patId = 'hatch-' + z.name;
        for (const t of state.towers) {
          if (only === undefined ? t.id === activeId : t.id !== only) continue;
          const rings = towerRings(t), sel = isTowerSelected(t.id) ? 1 : 0;
          if (rings[k]) {
            feats.push({
              type: 'Feature',
              geometry: { type: 'Polygon', coordinates: [rings[k]] },
              properties: {
                fill: z.fill,
                stroke: sel ? z.stroke : 'transparent',
                sel,
                pattern: patId,
                isBest: 1
              }
            });
          }
        }
      }
    }
    return { type: 'FeatureCollection', features: feats };
  }

  function towerPoints() {
    return { type: 'FeatureCollection', features: state.towers.map((t0) => {
      const t = previewOf(t0);
      return { type: 'Feature', geometry: { type: 'Point', coordinates: [t.lon, t.lat] },
        properties: { id: t.id, name: t.name, color: 'rgb(' + t.color.slice(0, 3).join(',') + ')',
          sel: isTowerSelected(t.id) ? 1 : 0 } };
    }) };
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
  let lblCanvas = null, lblCtx = null, deckKey = '';
  const srcTimers = new Map(), srcLast = new Map();

  function setData(id, data, fast) {
    const s = map && map.getSource(id);
    if (!s) return;
    if (!fast) {
      const t = srcTimers.get(id); if (t) { clearTimeout(t); srcTimers.delete(id); }
      srcLast.delete(id);
      s.setData(data);
      drawLabels();
      return;
    }
    const now = Date.now(), last = srcLast.get(id) || 0;
    if (now - last >= 28) {
      const t = srcTimers.get(id); if (t) { clearTimeout(t); srcTimers.delete(id); }
      srcLast.set(id, now);
      s.setData(data);
      drawLabels();
    } else {
      const prev = srcTimers.get(id); if (prev) clearTimeout(prev);
      srcTimers.set(id, setTimeout(() => {
        srcTimers.delete(id);
        srcLast.set(id, Date.now());
        const s2 = map && map.getSource(id);
        if (s2) s2.setData(data);
        drawLabels();
      }, 28 - (now - last)));
    }
  }

  function ensureLabelCanvas() {
    if (lblCanvas || !map || typeof document === 'undefined') return;
    const c = document.createElement('canvas');
    if (!c || typeof c.getContext !== 'function') return;
    c.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:2;';
    const parent = document.getElementById('map');
    if (parent && parent.appendChild) parent.appendChild(c);
    lblCanvas = c;
    lblCtx = c.getContext('2d');
  }

  function drawLabels() {
    ensureLabelCanvas();
    if (!lblCtx || !map) return;
    const dpr = window.devicePixelRatio || 1;
    const w = lblCanvas.clientWidth || window.innerWidth || 0;
    const h = lblCanvas.clientHeight || window.innerHeight || 0;
    if (!w || !h) return;
    if (lblCanvas.width !== Math.round(w * dpr) || lblCanvas.height !== Math.round(h * dpr)) {
      lblCanvas.width = Math.round(w * dpr);
      lblCanvas.height = Math.round(h * dpr);
    }
    const ctx = lblCtx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const th = theme();
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3.2;
    ctx.strokeStyle = th.halo;
    const zoom = map.getZoom ? map.getZoom() : 15;
    if (state.net && state.net.links && state.net.links.length) {
      ctx.font = '600 10.5px "Segoe UI", system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const l of state.net.links) {
        const isSel = l.id === state.selLink;
        if (!isSel && zoom < 14.2) continue;
        const a = devPos(l.a_dev, l.a), b = devPos(l.b_dev, l.b);
        const p = map.project([(a[1] + b[1]) / 2, (a[0] + b[0]) / 2]);
        if (p.x < -80 || p.x > w + 80 || p.y < -40 || p.y > h + 40) continue;
        const u = linkUtil(l.id);
        const txt = String(l.label || '') + (u !== null ? ' · ' + Math.round(u * 100) + '%' : '');
        if (!txt) continue;
        ctx.fillStyle = l.state === 'down' ? '#ff8a8e' : (l.state === 'warn' ? '#f5a524' : th.text);
        ctx.strokeText(txt, p.x, p.y - 7);
        ctx.fillText(txt, p.x, p.y - 7);
      }
    }
    if ((!deckOverlay || typeof deck === 'undefined') && state.towers && state.towers.length) {
      ctx.font = '600 11.5px "Segoe UI", system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = th.text;
      for (const t0 of state.towers) {
        const t = previewOf(t0);
        const p = map.project([t.lon, t.lat]);
        if (p.x < -100 || p.x > w + 100 || p.y < -40 || p.y > h + 40) continue;
        ctx.strokeText(t.name, p.x + 11, p.y);
        ctx.fillText(t.name, p.x + 11, p.y);
      }
    }
  }

  function renderCoverage() {
    setData('coverage', coverageFeatures());
    setData('coverage-active', activeId === null ? EMPTY : coverageFeatures(activeId));
    setData('towers', towerPoints());
    setData('netlinks', linkFeatures());
    renderDeck(); renderAzimuthHandle();
  }
  /* Во время поворота/переноса меняется одна вышка: обновляем только её источник и точки,
     а не перестраиваем полигоны всех вышек на каждое движение мыши. */
  function setActive(id) {
    if (activeId === id) return;
    activeId = id;
    setData('coverage', coverageFeatures());
  }
  function renderActive() {
    setData('coverage-active', activeId === null ? EMPTY : coverageFeatures(activeId), true);
    setData('towers', towerPoints(), true);
    renderDeck();
  }
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
    const show = t && typeof t.lat === 'number' && typeof t.lon === 'number' &&
                 isFinite(t.lat) && isFinite(t.lon) && t.beamwidth < 360 && state.mode === 'navigate';
    if (!show) {
      if (azMarker) { azMarker.remove(); azMarker = null; }
      setData('azline', EMPTY);
      return;
    }
    if (azDragging) return;
    const az = state.azPreview && state.azPreview.id === t.id ? state.azPreview.azimuth : t.azimuth;
    const pos = handleLngLat(t, az);
    if (!pos || !isFinite(pos[0]) || !isFinite(pos[1])) {
      if (azMarker) { azMarker.remove(); azMarker = null; }
      setData('azline', EMPTY);
      return;
    }
    if (!azMarker) {
      const el = document.createElement('div');
      el.className = 'az-handle';
      el.innerHTML = '<span class="knob"></span><span class="az-text"></span>';
      azMarker = new maplibregl.Marker({ element: el, draggable: true }).setLngLat(pos).addTo(map);
      azMarker.on('drag', onAzDrag);
      azMarker.on('dragend', onAzEnd);
    } else azMarker.setLngLat(pos);
    const txt = azMarker.getElement().querySelector('.az-text');
    if (txt) txt.textContent = Math.round(az) + '°';
    setData('azline', lineFrom([t.lon, t.lat], pos));
  }
  function onAzDrag() {
    const t = selectedTower();
    if (!t || !azMarker) return;
    azDragging = true;
    const ll = azMarker.getLngLat();
    const az = Math.round(U.bearing(t.lat, t.lon, ll.lat, ll.lng)) % 360;
    state.azPreview = { id: t.id, azimuth: az };
    setActive(t.id);
    renderActive();
    setData('azline', lineFrom([t.lon, t.lat], [ll.lng, ll.lat]));
    azMarker.getElement().querySelector('.az-text').textContent = az + '°';
  }
  function onAzEnd() {
    const t = selectedTower();
    azDragging = false;
    if (!t || !state.azPreview) return;
    send({ type: 'azimuth', id: t.id, azimuth: state.azPreview.azimuth });
    clearTimeout(azTimer);                  // если Python не ответил — вернём прежний вид
    azTimer = setTimeout(() => {
      if (!azDragging && state.azPreview) { state.azPreview = null; activeId = null; renderCoverage(); }
    }, 3000);
  }

  /* ---------------- перенос вышки мышью (режим «Навигация») ---------------- */
  function startTowerDrag(t, e) {
    state.towerDrag = { id: t.id, lat: t.lat, lon: t.lon, moved: false, x: e.point.x, y: e.point.y,
      dLat: t.lat - e.lngLat.lat, dLon: t.lon - e.lngLat.lng };
  }
  function moveTowerDrag(e) {
    const g = state.towerDrag;
    if (!g.moved && Math.hypot(e.point.x - g.x, e.point.y - g.y) < 4) return;
    if (!g.moved) { g.moved = true; setActive(g.id); if (map) map.getCanvas().style.cursor = 'grabbing'; }
    g.lat = e.lngLat.lat + g.dLat;
    g.lon = e.lngLat.lng + g.dLon;
    renderActive();
    moveDevicePreview('T' + g.id, g.lat, g.lon);              // кабели БС тянутся за вышкой
    if (azMarker) { azMarker.remove(); azMarker = null; setData('azline', EMPTY); }
  }
  function endTowerDrag() {
    const g = state.towerDrag;
    if (map) map.getCanvas().style.cursor = '';
    if (!g || !g.moved) { state.towerDrag = null; return; }
    g.done = true;                          // предпросмотр держим, пока Python не пришлёт пересчёт
    g.justEnded = true;
    send({ type: 'tower_move', id: g.id, lat: g.lat, lon: g.lon });
    clearTimeout(dragTimer);                // Python пришлёт пересчитанную вышку; если нет — вернуть на место
    dragTimer = setTimeout(() => {
      if (state.towerDrag === g) { state.towerDrag = null; activeId = null; renderCoverage(); }
    }, 4000);
  }


  /* ---------------- сеть: устройства, кабели, трафик ---------------- */
  const utilColor = (u) => 'hsl(' + Math.round(120 * (1 - Math.min(Math.max(u, 0), 1))) + ',85%,48%)';
  const linkUtil = (id) => (state.traffic && state.traffic.linkUtil && state.traffic.linkUtil[id] !== undefined)
    ? state.traffic.linkUtil[id] : null;

  function devPos(devId, fallback) {
    if (typeof devId === 'string' && devId.startsWith('T')) {
      const tid = Number(devId.slice(1));
      const t0 = state.towers.find((x) => x.id === tid);
      if (t0) {
        const t = previewOf(t0);
        if (typeof t.lat === 'number' && typeof t.lon === 'number' && isFinite(t.lat) && isFinite(t.lon)) {
          return [t.lat, t.lon];
        }
      }
    }
    const d = state.net.devices.find((x) => x.id === devId);
    if (d && typeof d.lat === 'number' && typeof d.lon === 'number' && isFinite(d.lat) && isFinite(d.lon)) {
      return [d.lat, d.lon];
    }
    if (fallback && typeof fallback[0] === 'number' && typeof fallback[1] === 'number' &&
        isFinite(fallback[0]) && isFinite(fallback[1])) {
      return fallback;
    }
    return [0, 0];
  }

  function linkFeatures() {
    const list = [];
    for (const l of state.net.links) {
      const a = devPos(l.a_dev, l.a), b = devPos(l.b_dev, l.b);
      if (!a || !b || !isFinite(a[0]) || !isFinite(a[1]) || !isFinite(b[0]) || !isFinite(b[1])) continue;
      const u = linkUtil(l.id);
      const col = (u !== null && l.state !== 'down') ? utilColor(u) : (l.state === 'warn' ? '#f5a524' : l.color);
      list.push({ type: 'Feature', properties: { id: l.id, state: l.state, col, width: l.width,
        sel: l.id === state.selLink ? 1 : 0,
        lbl: (l.label || '') + (u !== null ? ' · ' + Math.round(u * 100) + '%' : '') },
        geometry: { type: 'LineString', coordinates: [[a[1], a[0]], [b[1], b[0]]] } });
    }
    return { type: 'FeatureCollection', features: list };
  }

  /* Устройство тащат: двигаем концы его кабелей сразу (раньше кабель «прыгал» только после отпускания). */
  function moveDevicePreview(id, lat, lon) {
    const dev = state.net.devices.find((x) => x.id === id);
    if (dev) { dev.lat = lat; dev.lon = lon; }
    let hit = false;
    for (const l of state.net.links) {
      if (l.a_dev === id) { l.a = [lat, lon]; hit = true; }
      if (l.b_dev === id) { l.b = [lat, lon]; hit = true; }
    }
    if (hit) setData('netlinks', linkFeatures(), true);
  }

  function renderNet() {
    setData('netlinks', linkFeatures());
    if (!map) return;
    const ids = new Set(state.net.devices.filter((d) => !d.tower).map((d) => d.id));
    for (const [id, m] of devMarkers) if (!ids.has(id)) { m.remove(); devMarkers.delete(id); }
    for (const d of state.net.devices) {
      if (d.tower) continue;
      if (typeof d.lon !== 'number' || typeof d.lat !== 'number' || !isFinite(d.lon) || !isFinite(d.lat)) continue;
      let m = devMarkers.get(d.id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'net-dev';
        if (el.dataset) el.dataset.devId = d.id;
        el.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#fff" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round">' + (ROLE_ICONS[d.role] || '') + '</svg><span class="nm"></span>';
        m = new maplibregl.Marker({ element: el, draggable: canDrag() }).setLngLat([d.lon, d.lat]).addTo(map);
        m.on('drag', () => { const ll = m.getLngLat(); moveDevicePreview(d.id, ll.lat, ll.lng); });
        m.on('dragend', () => { const ll = m.getLngLat(); send({ type: 'net_move', id: d.id, lat: ll.lat, lon: ll.lng }); });
        devMarkers.set(d.id, m);
      } else {
        m.setLngLat([d.lon, d.lat]);
      }
      const el = m.getElement();
      el.classList.toggle('movable', canDrag());
      el.style.setProperty('--c', ROLE_COLORS[d.role] || '#888');
      const nmEl = el.querySelector('.nm');
      if (nmEl) nmEl.textContent = d.name;
      el.classList.toggle('selected', d.id === state.selDevice);
      el.classList.toggle('unpowered', d.powered === false);
    }
    // подписи кабелей и вышек рисуются на одном 2D-холсте поверх карты, без DOM-маркеров и без symbol-воркеров
    renderFlowDots(performance.now(), false);
    renderArm();
    drawLabels();
  }

  function renderArm() {
    const d = state.cableFrom && state.net.devices.find((x) => x.id === state.cableFrom);
    if (!d || !map) {
      if (armMarker) { armMarker.remove(); armMarker = null; }
      return;
    }
    const pos = devPos(d.id, [d.lat, d.lon]);
    if (!pos || typeof pos[0] !== 'number' || typeof pos[1] !== 'number' || !isFinite(pos[0]) || !isFinite(pos[1])) {
      if (armMarker) { armMarker.remove(); armMarker = null; }
      return;
    }
    const targetLngLat = [pos[1], pos[0]];
    if (!armMarker) {
      const el = document.createElement('div');
      el.className = 'arm-ring';
      armMarker = new maplibregl.Marker({ element: el }).setLngLat(targetLngLat).addTo(map);
    } else {
      armMarker.setLngLat(targetLngLat);
    }
  }

  /* Устройства перетаскиваются только в режиме навигации: в режимах «Кабель»/PDU малейшее
     движение мыши при клике превращало клик в перетаскивание — устройство сдвигалось, а кабель не создавался. */
  const canDrag = () => state.mode === 'navigate';
  function updateDraggable() {
    for (const m of devMarkers.values()) {
      if (m.setDraggable) m.setDraggable(canDrag());
      m.getElement().classList.toggle('movable', canDrag());
    }
  }

  /* Клик пришёлся прямо на значок устройства — берём его без поиска по расстоянию. */
  function deviceFromEvent(e) {
    const tgt = e && e.originalEvent && e.originalEvent.target;
    const el = tgt && tgt.closest ? tgt.closest('.net-dev') : null;
    const id = el && el.dataset ? el.dataset.devId : null;
    return id ? state.net.devices.find((x) => x.id === id) || null : null;
  }

  function pickDevice(point, e) {
    const direct = deviceFromEvent(e);
    if (direct) return direct;
    let best = null, bestD = 26;
    for (const d of state.net.devices) {
      const pos = devPos(d.id, [d.lat, d.lon]);
      const p = map.project([pos[1], pos[0]]);
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
      const a = devPos(l.a_dev, l.a), b = devPos(l.b_dev, l.b);
      const d = segDist(point, map.project([a[1], a[0]]), map.project([b[1], b[0]]));
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

  function renderFlowDots(now, fast) {
    const feats = [];
    // 1. Пакеты по кабелям транспортной сети
    if (state.traffic && state.traffic.linkUtil) {
      for (const l of state.net.links) {
        const u = linkUtil(l.id);
        if (u === null || u <= 0.01 || l.state === 'down') continue;
        const a = devPos(l.a_dev, l.a), b = devPos(l.b_dev, l.b);
        if (!a || !b || !isFinite(a[0]) || !isFinite(b[0])) continue;
        const n = 1 + Math.round(u * 5), period = 2600 - u * 1700;
        for (let k = 0; k < n; k++) {
          const f = ((now / period) + k / n) % 1;
          const g = 1 - (((now / period) * 0.8 + (k + 0.5) / n) % 1);
          feats.push({ type: 'Feature', properties: { col: utilColor(u) }, geometry: { type: 'Point',
            coordinates: [a[1] + (b[1] - a[1]) * f, a[0] + (b[0] - a[0]) * f] } });
          if (k % 2 === 0) feats.push({ type: 'Feature', properties: { col: utilColor(u) }, geometry: { type: 'Point',
            coordinates: [a[1] + (b[1] - a[1]) * g, a[0] + (b[0] - a[0]) * g] } });
        }
      }
    }
    // 2. Пакеты по радиоканалу: от базовой станции к пользователям и обратно
    if (state.traffic && state.traffic.lines && state.traffic.lines.length) {
      const period = 1300;
      const lines = state.traffic.lines;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const ulat = line[0], ulon = line[1], tlat = line[2], tlon = line[3], st = line[4];
        if (!isFinite(ulat) || !isFinite(ulon) || !isFinite(tlat) || !isFinite(tlon)) continue;
        const col = st === 2 ? '#f5a524' : '#2ecc71';
        // Downlink пакет (БС -> абонент)
        const fDl = ((now / period) + (i * 0.17)) % 1;
        feats.push({
          type: 'Feature', properties: { col },
          geometry: { type: 'Point', coordinates: [tlon + (ulon - tlon) * fDl, tlat + (ulat - tlat) * fDl] }
        });
        // Uplink пакет (абонент -> БС)
        if (i % 2 === 0) {
          const fUl = 1 - (((now / (period * 1.15)) + (i * 0.23)) % 1);
          feats.push({
            type: 'Feature', properties: { col },
            geometry: { type: 'Point', coordinates: [tlon + (ulon - tlon) * fUl, tlat + (ulat - tlat) * fUl] }
          });
        }
      }
    }
    setData('flow', { type: 'FeatureCollection', features: feats }, fast !== undefined ? fast : true);
  }
  let lastFlow = 0;
  function flowTick(now) {
    flowReq = 0;
    if (!state.traffic) { renderFlowDots(now, false); return; }
    if (now - lastFlow >= 40) {
      lastFlow = now;
      renderFlowDots(now, true);
    }
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
    if (!d || !state.mouse || typeof state.mouse.lng !== 'number' || typeof state.mouse.lat !== 'number' ||
        !isFinite(state.mouse.lng) || !isFinite(state.mouse.lat)) {
      renderDraft();
      return;
    }
    const pos = devPos(d.id, [d.lat, d.lon]);
    if (!pos || !isFinite(pos[0]) || !isFinite(pos[1])) { renderDraft(); return; }
    setData('draft', lineFrom([pos[1], pos[0]], [state.mouse.lng, state.mouse.lat]), true);
  }

  function renderDeck() {
    if (!deckOverlay || typeof deck === 'undefined') return;
    const list = state.towers.map(previewOf);
    const key = list.map((t) => t.id + ':' + t.lat + ':' + t.lon + ':' + t.height + ':' + t.tilt + ':' + t.scale + ':' + (t.id === state.selectedId ? '1' : '0')).join('|');
    if (key === deckKey) return;
    deckKey = key;
    const layers = [];
    const plain = list.filter((t) => !t.model_b64);
    const models = list.filter((t) => t.model_b64);
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
    // Оголовок антенны прямо на крыше здания / вершине вышки в 3D (Z = d.height)
    if (list.length && deck.ScatterplotLayer) {
      layers.push(new deck.ScatterplotLayer({
        id: 'tower-heads-3d',
        data: list,
        getPosition: (d) => [d.lon, d.lat, d.height],
        getRadius: 8,
        radiusUnits: 'pixels',
        getFillColor: (d) => d.color,
        getLineColor: (d) => d.id === state.selectedId ? [62, 139, 255, 255] : [255, 255, 255, 255],
        getLineWidth: (d) => d.id === state.selectedId ? 4 : 2.5,
        lineWidthUnits: 'pixels',
        stroked: true,
        filled: true,
        pickable: false,
      }));
    }
    // 3D подпись вышки на уровне оголовка антенны на крыше
    if (list.length && deck.TextLayer) {
      layers.push(new deck.TextLayer({
        id: 'tower-labels-3d',
        data: list,
        getPosition: (d) => [d.lon, d.lat, d.height],
        getText: (d) => d.name,
        getSize: 12,
        getColor: state.theme === 'light' ? [28, 36, 48, 255] : [230, 234, 240, 255],
        getTextAnchor: 'start',
        getAlignmentBaseline: 'center',
        getPixelOffset: [14, 0],
        fontFamily: '"Segoe UI", system-ui, sans-serif',
        fontWeight: 600,
        background: true,
        backgroundColor: state.theme === 'light' ? [255, 255, 255, 220] : [16, 20, 26, 220],
        backgroundPadding: [4, 2],
        pickable: false,
      }));
    }
    deckOverlay.setProps({ layers });
  }

  /* ---------------- узоры штриховки и слои карты ---------------- */
  function createHatchImage(strokeColor, bgColor, angleDeg, lineWidth, step) {
    const size = 32;
    const c = document.createElement('canvas');
    c.width = size; c.height = size;
    const ctx = c.getContext('2d');
    if (bgColor) {
      ctx.fillStyle = bgColor;
      ctx.fillRect(0, 0, size, size);
    }
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth || 2.5;
    ctx.lineCap = 'square';
    const rad = (angleDeg || 45) * Math.PI / 180;
    const cos = Math.cos(rad), sin = Math.sin(rad);
    for (let x = -size * 2; x <= size * 3; x += (step || 8)) {
      ctx.beginPath();
      ctx.moveTo(x, -size);
      ctx.lineTo(x + size * 4 * cos, -size + size * 4 * sin);
      ctx.stroke();
    }
    return ctx.getImageData(0, 0, size, size);
  }

  function initPatterns() {
    if (!map) return;
    const isDark = (state.theme !== 'light');
    const baseBg = isDark ? '#141820' : '#ffffff';
    const defs = [
      { id: 'hatch-green', stroke: '#22c55e', bg: baseBg, angle: 45, width: 2.8, step: 8 },
      { id: 'hatch-excellent', stroke: '#84cc16', bg: baseBg, angle: 45, width: 2.3, step: 9 },
      { id: 'hatch-good', stroke: '#eab308', bg: baseBg, angle: -45, width: 2.2, step: 10 },
      { id: 'hatch-weak', stroke: '#f97316', bg: baseBg, angle: 0, width: 2.0, step: 11 },
    ];
    for (const d of defs) {
      try {
        if (map.hasImage(d.id)) map.removeImage(d.id);
        const img = createHatchImage(d.stroke, d.bg, d.angle, d.width, d.step);
        map.addImage(d.id, img);
      } catch (err) {
        console.warn('initPatterns error for ' + d.id + ':', err);
      }
    }
  }

  /* ---------------- слои карты (пересоздаются при смене подложки) ---------------- */
  function setupLayers() {
    if (map.getSource('coverage')) return;
    for (const id of ['coverage', 'coverage-active', 'towers', 'zones', 'draft', 'measure', 'azline',
      'netlinks', 'flow', 'trfusers', 'trflines']) map.addSource(id, { type: 'geojson', data: EMPTY });
    for (const src of ['coverage', 'coverage-active']) {
      map.addLayer({ id: src + '-fill', type: 'fill', source: src,
        filter: ['==', ['coalesce', ['get', 'pattern'], ''], ''],
        paint: { 'fill-color': ['get', 'fill'], 'fill-opacity': 0.75 } });
      map.addLayer({ id: src + '-hatch', type: 'fill', source: src,
        filter: ['!=', ['coalesce', ['get', 'pattern'], ''], ''],
        paint: { 'fill-pattern': ['get', 'pattern'], 'fill-opacity': 0.95 } });
      map.addLayer({ id: src + '-outline', type: 'line', source: src,
        paint: { 'line-color': ['get', 'stroke'], 'line-opacity': 0.85,
          'line-width': ['case', ['==', ['get', 'sel'], 1], 3, 1.2] } });
    }
    try {
      map.addSource('openfreemap-buildings', { type: 'vector', url: 'https://tiles.openfreemap.org/planet' });
      map.addLayer({ id: '3d-buildings-extrusion', source: 'openfreemap-buildings', 'source-layer': 'building',
        type: 'fill-extrusion', minzoom: 15.5, paint: {
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
    map.addLayer({ id: 'tower-dot', type: 'circle', source: 'towers', paint: {
      'circle-radius': 3.5, 'circle-color': ['get', 'color'],
      'circle-opacity': 0.25,
      'circle-stroke-color': '#000000',
      'circle-stroke-width': 1,
      'circle-stroke-opacity': 0.25,
      'circle-pitch-alignment': 'viewport' } });
    deckKey = '';
    renderCoverage(); renderZones(); renderMeasure(); renderDraft(); renderNet(); renderTraffic();
  }

  const THEME_TEXT = { dark: { text: '#e6eaf0', halo: 'rgba(16,20,26,0.9)' }, light: { text: '#1c2430', halo: 'rgba(255,255,255,0.95)' } };
  const theme = () => THEME_TEXT[state.theme] || THEME_TEXT.dark;

  /* ---------------- режимы и взаимодействие ---------------- */
  function drawing() { return state.mode === 'zone_algo' || state.mode === 'zone_forbidden'; }

  function setMapMode(mode) {
    if (drawing() && state.mode !== mode) { state.draft = []; renderDraft(); }
    if (state.towerDrag && !state.towerDrag.done) { state.towerDrag = null; activeId = null; }
    state.mode = mode;
    state.cableFrom = null; renderArm(); renderDraft(); updateDraggable();
    const ind = $('mode-indicator');
    if (ind) { ind.textContent = HINTS[mode] || ''; ind.style.display = HINTS[mode] ? 'block' : 'none'; }
    if (map) {
      map.getCanvas().style.cursor = { select: 'pointer', place: 'cell', measure: 'crosshair', device: 'cell',
        cable: 'alias', probe: 'crosshair', zone_algo: 'crosshair', zone_forbidden: 'crosshair' }[mode] || '';
      if (drawing()) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    }
    renderAzimuthHandle();
  }

  function pickTower(point) {
    let best = null, bestD = 18;
    for (const t0 of state.towers) {
      const t = previewOf(t0);
      const p = map.project([t.lon, t.lat]);
      const d = Math.hypot(p.x - point.x, p.y - point.y);
      if (d < bestD) { bestD = d; best = t0; }
    }
    return best;
  }

  function finishZone() {
    const kind = state.mode === 'zone_algo' ? 'algo' : 'forbidden';
    if (state.draft.length >= 3) send({ type: 'zone', kind, polygon: state.draft.slice() });
    state.draft = []; renderDraft();
  }
  /* ---------------- Круговое меню (Ctrl+A / Quick Palette) ---------------- */
  const RADIAL_DATA = [
    {
      id: 'towers',
      label: 'Вышки и БС',
      icon: '🗼',
      color: '#ef4444',
      subitems: [
        { name: 'Huawei AAU 5G (64T64R)', type: 'tower', preset: 'Huawei AAU5613' },
        { name: 'Ericsson AIR 3268', type: 'tower', preset: 'Ericsson AIR 3268' },
        { name: 'Kathrein 80010865 (65°)', type: 'tower', preset: 'Kathrein 80010865' },
        { name: 'CommScope RVV-65D-R5', type: 'tower', preset: 'CommScope RVV-65D-R5' },
        { name: 'Базовая станция (круговая)', type: 'tower', preset: 'Базовая станция (круговая)' },
        { name: 'Сектор 120° (LTE)', type: 'tower', preset: 'Сектор 120°' }
      ]
    },
    {
      id: 'structures',
      label: 'Опоры и Каркасы',
      icon: '🏗️',
      color: '#f97316',
      subitems: [
        { name: 'Решётчатая башня 45 м', type: 'structure', kind: 'lattice', height: 45 },
        { name: 'Решётчатая башня 30 м', type: 'structure', kind: 'lattice', height: 30 },
        { name: 'Мачта на оттяжках 25 м', type: 'structure', kind: 'guyed', height: 25 },
        { name: 'Монополь 20 м', type: 'structure', kind: 'monopole', height: 20 },
        { name: 'Трубостойка на крышу 5 м', type: 'structure', kind: 'roof_mast', height: 5 },
        { name: 'Столб освещения 10 м', type: 'structure', kind: 'pole', height: 10 }
      ]
    },
    {
      id: 'switches',
      label: 'Коммутаторы',
      icon: '🔀',
      color: '#3b82f6',
      subitems: [
        { name: 'L2 Доступ, 24×1G PoE+', type: 'device', model: 'sw_l2_24poe' },
        { name: 'L2 Промышленный 8×PoE+', type: 'device', model: 'sw_l2_8poe' },
        { name: 'L2 Доступ, 48×1G PoE+', type: 'device', model: 'sw_l2_48poe' },
        { name: 'L3 Агрегация 24×10G SFP+', type: 'device', model: 'sw_l3_agg10' },
        { name: 'Multi-Gig 24×(до 10G) PoE++', type: 'device', model: 'sw_mgig_24bt' }
      ]
    },
    {
      id: 'routers',
      label: 'Маршрутизаторы',
      icon: '🌐',
      color: '#06b6d4',
      subitems: [
        { name: 'Cell Site Router (CSR)', type: 'device', model: 'rt_csr' },
        { name: 'Агрегирующий роутер', type: 'device', model: 'rt_agg' },
        { name: 'Маршрутизатор ядра (PE)', type: 'device', model: 'rt_pe' },
        { name: 'Межсетевой экран (NGFW)', type: 'device', model: 'fw_ngfw' }
      ]
    },
    {
      id: 'core',
      label: 'Ядро и Серверы',
      icon: '🖥️',
      color: '#10b981',
      subitems: [
        { name: 'Ядро сети (5GC / EPC)', type: 'device', model: 'core_5gc' },
        { name: 'Макро-БС 5G NR (gNB)', type: 'device', model: 'bs_macro_5g' },
        { name: 'Макро-БС LTE (eNodeB)', type: 'device', model: 'bs_macro_lte' },
        { name: 'Малая сота (Small Cell)', type: 'device', model: 'bs_small' }
      ]
    },
    {
      id: 'radio_ptp',
      label: 'РРЛ Линии',
      icon: '📡',
      color: '#ec4899',
      subitems: [
        { name: 'РРЛ 6–42 ГГц (традиционная)', type: 'device', model: 'mw_trad' },
        { name: 'РРЛ E-band 71–86 ГГц (10G)', type: 'device', model: 'mw_eband' },
        { name: 'РРЛ V-band 57–66 ГГц (PoE)', type: 'device', model: 'mw_vband' }
      ]
    }
  ];

  function polarToCartesian(cx, cy, r, angleInDegrees) {
    const angleInRadians = (angleInDegrees - 90) * Math.PI / 180.0;
    return {
      x: cx + (r * Math.cos(angleInRadians)),
      y: cy + (r * Math.sin(angleInRadians))
    };
  }

  function describeDonutSegment(cx, cy, rIn, rOut, startAngle, endAngle) {
    const p1 = polarToCartesian(cx, cy, rOut, startAngle);
    const p2 = polarToCartesian(cx, cy, rOut, endAngle);
    const p3 = polarToCartesian(cx, cy, rIn, endAngle);
    const p4 = polarToCartesian(cx, cy, rIn, startAngle);
    const largeArc = endAngle - startAngle <= 180 ? '0' : '1';
    return [
      'M', p1.x, p1.y,
      'A', rOut, rOut, 0, largeArc, 1, p2.x, p2.y,
      'L', p3.x, p3.y,
      'A', rIn, rIn, 0, largeArc, 0, p4.x, p4.y,
      'Z'
    ].join(' ');
  }

  function buildRadialMenu() {
    const svg = $('radial-svg');
    if (!svg) return;
    svg.innerHTML = '';
    const n = RADIAL_DATA.length;
    const stepDeg = 360 / n;
    const cx = 220, cy = 220;
    const rIn = 52, rOut = 142;
    const isLight = (state.theme === 'light');
    const bgFill = isLight ? 'rgba(255, 255, 255, 0.94)' : 'rgba(23, 28, 36, 0.94)';
    const strokeCol = isLight ? '#d3dae4' : '#2b3443';
    const textCol = isLight ? '#1c2430' : '#e6eaf0';

    RADIAL_DATA.forEach((cat, i) => {
      const startAngle = i * stepDeg + 1.5;
      const endAngle = (i + 1) * stepDeg - 1.5;
      const midAngle = (startAngle + endAngle) / 2;

      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.className.baseVal = 'radial-slice';

      // Основной лепесток
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', describeDonutSegment(cx, cy, rIn, rOut, startAngle, endAngle));
      path.setAttribute('class', 'slice-bg');
      path.setAttribute('fill', bgFill);
      path.setAttribute('stroke', strokeCol);
      path.setAttribute('stroke-width', '1.2');
      g.appendChild(path);

      // Внешний цветной акцентный кант
      const arcR = rOut - 1.5;
      const a1 = polarToCartesian(cx, cy, arcR, startAngle + 2);
      const a2 = polarToCartesian(cx, cy, arcR, endAngle - 2);
      const arcPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      arcPath.setAttribute('d', ['M', a1.x, a1.y, 'A', arcR, arcR, 0, 0, 1, a2.x, a2.y].join(' '));
      arcPath.setAttribute('fill', 'none');
      arcPath.setAttribute('stroke', cat.color);
      arcPath.setAttribute('stroke-width', '3.5');
      arcPath.setAttribute('stroke-linecap', 'round');
      g.appendChild(arcPath);

      // Позиция центра сектора
      const pos = polarToCartesian(cx, cy, (rIn + rOut) / 2, midAngle);

      // Акцентная метка категории
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx', pos.x);
      dot.setAttribute('cy', pos.y - 7);
      dot.setAttribute('r', '3.5');
      dot.setAttribute('fill', cat.color);
      g.appendChild(dot);

      // Текстовая подпись
      const tText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      tText.setAttribute('x', pos.x);
      tText.setAttribute('y', pos.y + 11);
      tText.setAttribute('text-anchor', 'middle');
      tText.setAttribute('dominant-baseline', 'central');
      tText.setAttribute('font-size', '10.5');
      tText.setAttribute('font-weight', '600');
      tText.setAttribute('fill', textCol);
      tText.textContent = cat.label;
      g.appendChild(tText);

      g.addEventListener('mouseenter', () => {
        path.setAttribute('fill', isLight ? 'rgba(31, 111, 224, 0.12)' : 'rgba(62, 139, 255, 0.18)');
        path.setAttribute('stroke', isLight ? '#1f6fe0' : '#3e8bff');
        showRadialSubMenu(cat, midAngle);
      });
      g.addEventListener('mouseleave', () => {
        path.setAttribute('fill', bgFill);
        path.setAttribute('stroke', strokeCol);
      });

      svg.appendChild(g);
    });
  }

  function showRadialSubMenu(cat, midAngle) {
    const box = $('radial-sub-box');
    if (!box) return;
    const rad = (midAngle - 90) * Math.PI / 180;
    const dist = 162;
    const cx = 220 + dist * Math.cos(rad);
    const cy = 220 + dist * Math.sin(rad);

    box.style.left = Math.max(10, Math.min(270, cx - 110)) + 'px';
    box.style.top = Math.max(10, Math.min(270, cy - 60)) + 'px';
    box.innerHTML = '<div class="radial-sub-header"><span class="sub-dot" style="background:' + cat.color + '"></span><span>' + cat.label + '</span><span class="sub-count">' + cat.subitems.length + '</span></div>';

    cat.subitems.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'radial-sub-item';
      let tag = item.type === 'tower' ? 'Антенна' : (item.type === 'structure' ? (item.height + ' м') : 'Сеть');
      row.innerHTML = '<span class="item-name">' + item.name + '</span><span class="sub-tag">' + tag + '</span>';
      row.addEventListener('click', (ev) => {
        ev.stopPropagation();
        closeRadialMenu();
        const pos = radialLngLat || (state.mouse || map.getCenter());
        send({
          type: 'radial_select',
          item: item,
          lat: pos.lat,
          lon: pos.lng !== undefined ? pos.lng : pos.lon
        });
      });
      box.appendChild(row);
    });
    box.style.display = 'block';
  }

  function closeRadialMenu() {
    const ov = $('radial-overlay');
    if (ov) ov.style.display = 'none';
    const box = $('radial-sub-box');
    if (box) box.style.display = 'none';
  }

  function openRadialMenu(x, y) {
    const ov = $('radial-overlay');
    const menu = $('radial-menu');
    if (!ov || !menu) return;
    radialLngLat = state.mouse || map.getCenter();
    const cx = Math.max(230, Math.min(window.innerWidth - 230, x || window.innerWidth / 2));
    const cy = Math.max(230, Math.min(window.innerHeight - 230, y || window.innerHeight / 2));
    menu.style.left = cx + 'px';
    menu.style.top = cy + 'px';
    buildRadialMenu();
    ov.style.display = 'block';
    const box = $('radial-sub-box');
    if (box) box.style.display = 'none';
  }

  function undoLast() {
    if (state.cableFrom) { state.cableFrom = null; renderArm(); renderDraft(); return; }   // отмена начатого кабеля
    if (state.mode === 'measure') { state.measure.pop(); renderMeasure(); send({ type: 'measure', points: state.measure.slice() }); }
    else if (drawing()) { state.draft.pop(); renderDraft(); }
  }

  function onClick(e) {
    const lat = e.lngLat.lat, lon = e.lngLat.lng;
    switch (state.mode) {
      case 'navigate': case 'select': {
        if (state.towerDrag && state.towerDrag.justEnded) { state.towerDrag.justEnded = false; break; }
        const t = deviceFromEvent(e) ? null : pickTower(e.point);
        if (t) { send({ type: 'select', id: t.id }); break; }
        const d = pickDevice(e.point, e);
        if (d) { state.selDevice = d.id; state.selLink = null; renderNet(); send({ type: 'net_select', id: d.id }); break; }
        const l = pickLink(e.point);
        if (l) { state.selLink = l.id; state.selDevice = null; renderNet(); send({ type: 'net_link_select', id: l.id }); break; }
        if (state.mode === 'select') send({ type: 'select', id: null });
        break;
      }
      case 'device': {
        const pitch = map.getPitch ? map.getPitch() : 0;
        const bearing = map.getBearing ? map.getBearing() : 0;
        send({ type: 'net_place', lat, lon, pitch, bearing });
        break;
      }
      case 'cable': case 'pdu': {
        const d = pickDevice(e.point, e);
        if (!d) {
          if (state.cableFrom) { state.cableFrom = null; renderArm(); renderDraft(); }
          break;
        }
        if (!state.cableFrom) { state.cableFrom = d.id; renderArm(); break; }
        if (d.id === state.cableFrom) break;
        const a = state.cableFrom;
        state.cableFrom = null; renderArm(); renderDraft();
        send({ type: state.mode === 'cable' ? 'net_connect' : 'net_pdu', a, b: d.id });
        break;
      }
      case 'place': {
        const pitch = map.getPitch ? map.getPitch() : 0;
        const bearing = map.getBearing ? map.getBearing() : 0;
        let bldHeight = 0;
        try {
          const feats = map.queryRenderedFeatures(e.point, { layers: ['3d-buildings-extrusion'] });
          if (feats && feats.length) {
            bldHeight = feats[0].properties.render_height || feats[0].properties.height || 15;
          }
        } catch (err) {}
        send({ type: 'place', lat, lon, pitch, bearing, bld_height: bldHeight });
        break;
      }
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

  function onDblClick(e) {
    if (state.mode === 'navigate' && e && e.point) {          // двойной клик по вышке/устройству — SNMP
      if (state.towerDrag && !state.towerDrag.moved) state.towerDrag = null;
      const d = deviceFromEvent(e);
      const t = d ? null : pickTower(e.point);
      const dev = d || (t ? null : pickDevice(e.point, e));
      if (t || dev) {
        if (e.preventDefault) e.preventDefault();             // без приближения карты
        send(t ? { type: 'snmp', tower: t.id } : { type: 'snmp', dev: dev.id });
        return;
      }
    }
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
    setTowers(list) {
      for (const t of list) {               // rays: null — «без изменений», берём из кэша
        const c = rayCache.get(t.id);
        if (t.rays === null || t.rays === undefined) t.rays = c ? c.rays : [];
        else rayCache.set(t.id, { rev: t.rev, rays: t.rays });
      }
      const ids = new Set(list.map((t) => t.id));
      for (const id of [...rayCache.keys()]) if (!ids.has(id)) { rayCache.delete(id); ringCache.delete(id); }
      state.towers = list; state.azPreview = null; state.towerDrag = null; activeId = null;
      clearTimeout(dragTimer);
      renderCoverage();
    },
    selectTower(id) {
      if (state.selectedId === id && state.azPreview === null) return;
      state.selectedId = id; state.azPreview = null; renderCoverage();
    },
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
      initPatterns();
      drawLabels();
      renderCoverage();
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
    setCoverageMode(mode) { state.coverageMode = mode; renderCoverage(); },
    openRadialMenu(x, y) { openRadialMenu(x, y); },
    getState() { return state; },
    project(lat, lon) { const p = map.project([lon, lat]); return [p.x, p.y]; },   // для проверок/отладки
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
    initPatterns();
    setupLayers();
    if (deckOverlay) {
      try { map.removeControl(deckOverlay); } catch (e) {}
      deckOverlay = null;
    }
    if (typeof deck !== 'undefined') {
      try {
        deckOverlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
        map.addControl(deckOverlay);
      } catch (e) {
        console.warn('deckOverlay init error: ' + e);
      }
    }
    deckKey = '';
    renderDeck();
    drawLabels();
    renderCoverage();
    renderNet();
    renderZones();
    renderMeasure();
    renderDraft();
    renderTraffic();
    if (!state.ready) {
      state.ready = true;
      send({ type: 'ready' });
    } else {
      send({ type: 'style_loaded' });
    }
  });
  map.on('render', drawLabels);
  map.on('error', (e) => { if (e && e.error) console.warn('map error: ' + e.error.message); });
  // окно Qt меняет размер (сплиттер, развёртывание) — подогнать холст, если ResizeObserver не сработал
  if (window.addEventListener) {
    window.addEventListener('resize', () => { map.resize(); drawLabels(); });
    window.addEventListener('mouseup', () => { if (state.towerDrag && !state.towerDrag.done) endTowerDrag(); });
  }
  map.on('zoom', () => { if (!azDragging) renderAzimuthHandle(); });
  map.on('mousedown', (e) => {              // перенос вышки или рамка выделения (Shift + drag)
    lastMousePoint = e.point ? { x: e.point.x, y: e.point.y } : null;
    if (e.originalEvent && (e.originalEvent.shiftKey || state.mode === 'select')) {
      const tgt = e.originalEvent && e.originalEvent.target;
      if (!tgt || (!tgt.closest('.az-handle') && !tgt.closest('.net-dev'))) {
        boxSelecting = true;
        boxStart = { x: e.point.x, y: e.point.y };
        map.dragPan.disable();
        if (e.preventDefault) e.preventDefault();
        return;
      }
    }
    if (state.mode !== 'navigate' || deviceFromEvent(e) || (e.originalEvent && e.originalEvent.button)) return;
    const tgt = e.originalEvent && e.originalEvent.target;
    if (tgt && tgt.closest && tgt.closest('.az-handle')) return;
    const t = pickTower(e.point);
    if (!t) return;
    if (e.preventDefault) e.preventDefault();                 // карту при этом не панорамируем
    startTowerDrag(t, e);
  });
  map.on('mouseup', (e) => {
    if (state.towerDrag && !state.towerDrag.done) endTowerDrag();
    if (boxSelecting) {
      boxSelecting = false;
      map.dragPan.enable();
      const selEl = $('selection-box');
      if (selEl) selEl.style.display = 'none';
      if (boxStart && e && e.point) {
        const minX = Math.min(boxStart.x, e.point.x), maxX = Math.max(boxStart.x, e.point.x);
        const minY = Math.min(boxStart.y, e.point.y), maxY = Math.max(boxStart.y, e.point.y);
        const w = maxX - minX, h = maxY - minY;
        boxStart = null;
        if (w > 8 && h > 8) {
          const selTowers = [];
          for (const t of state.towers) {
            const p = map.project([t.lon, t.lat]);
            if (p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY) selTowers.push(t.id);
          }
          const selDevs = [];
          if (state.net && state.net.devices) {
            for (const d of state.net.devices) {
              const p = map.project([d.lon, d.lat]);
              if (p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY) selDevs.push(d.id);
            }
          }
          state.selectedTowers = selTowers;
          state.selectedDevices = selDevs;
          if (selTowers.length > 0) state.selectedId = selTowers[0];
          renderCoverage();
          renderNet();
          send({ type: 'selection_box', tower_ids: selTowers, dev_ids: selDevs });
        }
      }
    }
  });
  map.on('click', onClick);
  map.on('dblclick', onDblClick);
  map.on('contextmenu', (e) => { e.preventDefault(); undoLast(); });

  let hoverThrottle = 0, hudThrottle = 0;
  map.on('mousemove', (e) => {
    if (!e || !e.lngLat) return;
    const now = Date.now();
    state.mouse = e.lngLat;
    lastMousePoint = e.point ? { x: e.point.x, y: e.point.y } : null;
    if (boxSelecting && boxStart) {
      const minX = Math.min(boxStart.x, e.point.x), maxX = Math.max(boxStart.x, e.point.x);
      const minY = Math.min(boxStart.y, e.point.y), maxY = Math.max(boxStart.y, e.point.y);
      const selEl = $('selection-box');
      if (selEl) {
        selEl.style.left = minX + 'px';
        selEl.style.top = minY + 'px';
        selEl.style.width = (maxX - minX) + 'px';
        selEl.style.height = (maxY - minY) + 'px';
        selEl.style.display = 'block';
      }
      return;
    }
    if (state.towerDrag && !state.towerDrag.done) {
      if (e.originalEvent && e.originalEvent.buttons !== undefined && (e.originalEvent.buttons & 1) === 0) {
        endTowerDrag();
      } else {
        moveTowerDrag(e);
      }
    }
    if (state.cableFrom) rubberBand();
    if (now - hudThrottle > 40) {
      hudThrottle = now;
      const h = $('hud-coords');
      if (h) h.textContent = e.lngLat.lat.toFixed(6) + ', ' + e.lngLat.lng.toFixed(6);
    }
    if ((state.mode === 'place' || state.mode === 'probe') && now - hoverThrottle > 1500) {   // предзагрузка зданий
      hoverThrottle = now;
      send({ type: 'hover_load', lat: e.lngLat.lat, lon: e.lngLat.lng });
    }
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      closeRadialMenu();
      state.draft = []; state.cableFrom = null; renderArm(); renderDraft(); clearAnim();
      send({ type: 'mode', mode: 'navigate' });
      return;
    }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'a' || ev.key === 'A' || ev.code === 'KeyA')) {
      const activeEl = document.activeElement;
      if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) return;
      ev.preventDefault();
      const p = lastMousePoint || { x: (window.innerWidth || 800) / 2, y: (window.innerHeight || 600) / 2 };
      openRadialMenu(p.x, p.y);
      return;
    }
    onKey(ev);
  });

  const radialOverlay = $('radial-overlay');
  if (radialOverlay) {
    radialOverlay.addEventListener('click', (ev) => {
      if (ev.target === radialOverlay || (ev.target.closest && ev.target.closest('#radial-center'))) {
        closeRadialMenu();
      }
    });
  }
})();
