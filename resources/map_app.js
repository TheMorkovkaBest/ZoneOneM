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
  };
  const EMPTY = { type: 'FeatureCollection', features: [] };

  const state = {
    mode: 'navigate', towers: [], selectedId: null, theme: 'dark', base: 'dark',
    zones: { algo: null, forbidden: [] }, measure: [], draft: [], rectShape: false,
    probe: null, ready: false, azPreview: null,
  };
  let map = null, deckOverlay = null;
  const towerMarkers = new Map();
  let measureMarkers = [], probeMarker = null, azMarker = null, azDragging = false, azTimer = null;

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
    for (const id of ['coverage', 'zones', 'draft', 'measure', 'azline']) map.addSource(id, { type: 'geojson', data: EMPTY });
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
    renderCoverage(); renderZones(); renderMeasure(); renderDraft();
  }

  /* ---------------- режимы и взаимодействие ---------------- */
  function drawing() { return state.mode === 'zone_algo' || state.mode === 'zone_forbidden'; }

  function setMapMode(mode) {
    if (drawing() && state.mode !== mode) { state.draft = []; renderDraft(); }
    state.mode = mode;
    const ind = $('mode-indicator');
    if (ind) { ind.textContent = HINTS[mode] || ''; ind.style.display = HINTS[mode] ? 'block' : 'none'; }
    if (map) {
      map.getCanvas().style.cursor = { select: 'pointer', place: 'cell', measure: 'crosshair',
        probe: 'crosshair', zone_algo: 'crosshair', zone_forbidden: 'crosshair' }[mode] || '';
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
        if (t || state.mode === 'select') send({ type: 'select', id: t ? t.id : null });
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
      state.draft = []; renderDraft();
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
  map.on('zoom', () => { if (!azDragging) renderAzimuthHandle(); });
  map.on('click', onClick);
  map.on('dblclick', onDblClick);
  map.on('contextmenu', (e) => { e.preventDefault(); undoLast(); });

  let hoverThrottle = 0, hudThrottle = 0;
  map.on('mousemove', (e) => {
    const now = Date.now();
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
