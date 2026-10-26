// Прогон resources/*.js в Node с заглушками MapLibre/DOM.  Вход (stdin): JSON с кейсами геодезии.
const fs = require('fs'), path = require('path'), vm = require('vm');
const res = (f) => fs.readFileSync(path.join(__dirname, '..', 'resources', f), 'utf8');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = { geo: [], checks: {}, messages: [] };

// ---------- геодезия ----------
const U = require('../resources/geo_utils.js');
for (const c of cases.geo) out.geo.push({ hav: U.haversine(...c), brg: U.bearing(...c) });
out.checks.fmt = [U.fmtDist(250), U.fmtDist(1500), U.fmtBearing(45)];

// ---------- заглушки ----------
global.window = global;
const handlers = {}, docHandlers = {};
global.addEventListener = (e, f) => { docHandlers[e] = f; };
const makeEl = () => {
  const el = { style: { setProperty(k, v) { this[k] = v; } }, textContent: '', innerHTML: '', _cls: new Set(), _kids: {},
    dataset: {}, setAttribute() {}, addEventListener() {},
    closest(sel) { return sel === '.' + String(el.className).split(' ')[0] ? el : null; },
    classList: { toggle(c, on) { on ? el._cls.add(c) : el._cls.delete(c); }, add(c) { el._cls.add(c); } },
    querySelector(sel) { return (el._kids[sel] = el._kids[sel] || makeEl()); } };
  return el;
};
global.document = { getElementById: () => makeEl(), createElement: makeEl,
  body: makeEl(), addEventListener: (e, f) => { docHandlers[e] = f; } };
global.console = { log: (m) => { if (String(m).startsWith('PY:')) out.messages.push(JSON.parse(m.slice(3))); },
  warn() {}, error() {} };

const frames = [];
global.requestAnimationFrame = (f) => { frames.push(f); return frames.length; };
global.cancelAnimationFrame = () => {};
let mapInst = null, markersAlive = 0;
const markers = [];
class Marker { constructor(o) { this.el = o.element; this.opt = o; this.h = {}; this.ll = null; this.alive = false; markers.push(this); }
  setLngLat(ll) { this.ll = ll; return this; } getLngLat() { return { lng: this.ll[0], lat: this.ll[1] }; }
  on(e, f) { this.h[e] = f; return this; }
  setDraggable(v) { this.opt.draggable = v; return this; }
  addTo() { markersAlive++; this.alive = true; return this; }
  remove() { if (this.alive) markersAlive--; this.alive = false; } getElement() { return this.el; } }
class Src { constructor(d) { this.data = d.data; } setData(d) { this.data = d; } }
class MapMock {
  constructor(o) { mapInst = this; this.sources = {}; this.layers = {}; this.style = o.style;
    this.canvas = { style: {} }; this.dbl = true; this.doubleClickZoom = { enable: () => (this.dbl = true), disable: () => (this.dbl = false) }; }
  on(e, f) { handlers[e] = f; } addControl() {}
  addSource(id, d) { this.sources[id] = new Src(d); } getSource(id) { return this.sources[id]; }
  addLayer(l) { this.layers[l.id] = l; } getLayer(id) { return this.layers[id]; }
  setPaintProperty() {} getCanvas() { return this.canvas; } getZoom() { return 15; }
  getStyle() { return { layers: [{ layout: { 'text-font': ['Noto Sans Regular'] } }] }; }
  project(p) { return { x: p[0] * 1000, y: p[1] * 1000 }; }
  flyTo() {} fitBounds() {}
  setStyle(s) { this.style = s; this.sources = {}; this.layers = {}; handlers['style.load'](); }
}
global.maplibregl = { Map: MapMock, Marker, NavigationControl: class {}, ScaleControl: class {} };

vm.runInThisContext(res('map_app.js'));
const fire = (e, ev) => handlers[e](ev);
const click = (lat, lon, x = 0, y = 0, target = null) =>
  fire('click', { lngLat: { lat, lng: lon }, point: { x, y }, originalEvent: target ? { target } : undefined });

fire('style.load');
out.checks.ready = out.messages.some((m) => m.type === 'ready');
out.checks.layers = ['coverage-fill', 'measure-line', 'zones-fill', 'draft-pts', '3d-buildings-extrusion'].every((l) => mapInst.getLayer(l));

// вышка: круг по 4 зонам, 180 лучей
const rays = []; for (let i = 0; i < 180; i++) rays.push([i * 2, 50, 100, 150, 200]);
const sector = []; for (let i = 0; i < 33; i++) sector.push([(345 + i * 2) % 360, 50, 100, 150, 200]);   // сектор через 0°
const T = (id, lat, lon, r, bw, az, rev = 1) => ({ id, name: 'T' + id, lat, lon, height: 5, tilt: 0, scale: 1,
  color: [255, 0, 0, 255], azimuth: az, beamwidth: bw, model_b64: null, rays: r, rev });
api.setTowers([T(1, 47.0, 28.0, rays, 360, 0), T(2, 47.1, 28.1, sector, 65, 0)]);
out.checks.coverageFeatures = mapInst.getSource('coverage').data.features.length;      // 2 вышки * 4 зоны
const sec = mapInst.getSource('coverage').data.features[7].geometry.coordinates[0];
out.checks.sectorRingStartsAtCenter = sec[0][0] === 28.1 && sec[0][1] === 47.1 && sec[sec.length - 1][0] === 28.1;
out.checks.sectorRingLen = sec.length;                                                    // центр + 33 + центр
// порядок точек сектора: первая точка — самый левый луч (345°)
out.checks.sectorFirstWest = sec[1][0] < 28.1;

// выбор вышки по пикселям
api.setMapMode('navigate'); out.messages.length = 0;
click(0, 0, 28.0 * 1000 + 5, 47.0 * 1000 + 5); click(0, 0, 5, 5);
out.checks.select = out.messages.map((m) => m.type + ':' + m.id);

// линейка
api.setMapMode('measure'); out.messages.length = 0;
const markersBefore = markersAlive;
click(47.0, 28.0); click(47.0, 28.01);
const meas = out.messages.filter((m) => m.type === 'measure').pop();
out.checks.measurePts = meas.points.length; out.checks.measureMarkers = markersAlive - markersBefore;
fire('contextmenu', { preventDefault() {} });
out.checks.measureUndo = out.messages.filter((m) => m.type === 'measure').pop().points.length;
api.clearMeasure();

// зона-полигон с двойным кликом
api.setMapMode('zone_algo'); out.messages.length = 0;
click(1, 1); click(1, 2); click(2, 2); click(2, 1); click(2, 1); fire('dblclick', {});
const z = out.messages.find((m) => m.type === 'zone');
out.checks.zonePoly = z && z.kind + ':' + z.polygon.length;
// прямоугольник
api.setRectShape(true); out.messages.length = 0;
api.setMapMode('zone_forbidden'); click(10, 10); click(11, 12);
const zr = out.messages.find((m) => m.type === 'zone');
out.checks.zoneRect = zr && zr.kind + ':' + zr.polygon.length + ':' + JSON.stringify(zr.polygon[2]);
api.setRectShape(false);
out.checks.dblZoomDisabled = mapInst.dbl === false;
api.setMapMode('navigate');
out.checks.dblZoomRestored = mapInst.dbl === true;

// размещение / зонд / hover
api.setMapMode('place'); out.messages.length = 0; click(47.5, 28.5);
api.setMapMode('probe'); click(47.6, 28.6);
fire('mousemove', { lngLat: { lat: 1, lng: 2 } });
out.checks.misc = out.messages.map((m) => m.type);

// Esc -> режим навигации
out.messages.length = 0; docHandlers.keydown({ key: 'Escape' });
out.checks.esc = out.messages.map((m) => m.type + ':' + m.mode);

// ручка азимута: есть только у секторной вышки, предпросмотр поворачивает сектор, отпускание шлёт азимут
const liveHandle = () => markers.find((m) => m.opt.draggable && m.alive);
api.setMapMode('navigate'); api.selectTower(1);
out.checks.noHandleForOmni = !liveHandle();
api.selectTower(2);
const hnd = liveHandle();
out.checks.handleExists = !!hnd;
out.messages.length = 0;
hnd.setLngLat([28.2, 47.1]); hnd.h.drag();                       // тянем строго на восток от вышки 2
const ringE = mapInst.getSource('coverage-active').data.features[3].geometry.coordinates[0];
out.checks.mainUntouched = mapInst.getSource('coverage').data.features.length;   // остальные вышки не перестраиваются
out.checks.previewEast = ringE.slice(1, -1).every((p) => p[0] > 28.1);
hnd.h.dragend();
out.checks.azMsg = out.messages.filter((m) => m.type === 'azimuth').map((m) => [m.id, m.azimuth]);
api.setTowers([T(1, 47.0, 28.0, rays, 360, 0), T(2, 47.1, 28.1, sector, 65, 90)]);   // ответ Python
out.checks.previewCleared = api.getState().azPreview === null;
api.setMapMode('measure');
out.checks.handleHiddenInMeasure = !liveHandle();
api.setMapMode('navigate');

// ---- сеть: устройства, кабель, PDU, выбор линка, анимация, трафик ----
const netDev = (id, role, lat, lon, tower) => ({ id, name: id, role, lat, lon, tower, powered: true, model: 'x' });
const NET = { devices: [netDev('T1', 'tower', 47.0, 28.0, true), netDev('D1', 'switch_l2', 47.0, 28.1, false),
                        netDev('D2', 'core', 47.0, 28.2, false)],
  links: [{ id: 'L1', a: [47.0, 28.0], b: [47.0, 28.1], a_dev: 'T1', b_dev: 'D1', state: 'up', color: '#4cc9f0', width: 3, label: '10G · 90 м', group: 'copper', speed: 10000 },
          { id: 'L2', a: [47.0, 28.1], b: [47.0, 28.2], a_dev: 'D1', b_dev: 'D2', state: 'down', color: '#ffb703', width: 2, label: '✕ 12000 м', group: 'fiber', speed: 0 }] };
api.setTowers([]);                       // башни-«пины» не мешают выбору устройств
api.setNetwork(NET);
out.checks.netLinkFeatures = mapInst.getSource('netlinks').data.features.length;
out.checks.netMarkers = markers.filter((m) => m.alive && m.el.className === 'net-dev').length;   // башни без своего маркера
out.checks.linkLabels = mapInst.getSource('netlinks').data.features.filter((f) => f.properties.lbl).length;
out.checks.linkLabelDom = markers.filter((m) => m.alive && m.el.className === 'link-lbl').length;   // подписи — слой, не DOM
api.setMapMode('cable'); out.messages.length = 0;
click(0, 0, 28000 + 3, 47000 - 2);       // у T1
fire('mousemove', { lngLat: { lat: 47.0, lng: 28.05 }, point: { x: 0, y: 0 } });
out.checks.rubberBand = mapInst.getSource('draft').data.features.some((f) => f.geometry.type === 'LineString');
click(0, 0, 28100 + 2, 47000 + 1);       // у D1
out.checks.cableMsg = out.messages.filter((m) => m.type === 'net_connect').map((m) => [m.a, m.b]);
// в режиме «Кабель» устройства не перетаскиваются (клик не превращается в перемещение)
const devMarker = (id) => markers.find((m) => m.alive && m.el.className === 'net-dev' && m.el.dataset.devId === id);
out.checks.dragInCable = devMarker('D1').opt.draggable;
// клик прямо по значку устройства далеко от его координат (значок крупный) — всё равно попадание
out.messages.length = 0;
click(0, 0, 0, 0, devMarker('D2').el); click(0, 0, 0, 0, devMarker('D1').el);
out.checks.cableByIcon = out.messages.filter((m) => m.type === 'net_connect').map((m) => [m.a, m.b]);
// ПКМ отменяет выбранное первое устройство
out.messages.length = 0;
click(0, 0, 28000 + 3, 47000 - 2); fire('contextmenu', { preventDefault() {} }); click(0, 0, 28100, 47000);
out.checks.cableCancelled = out.messages.filter((m) => m.type === 'net_connect').length;
api.setMapMode('navigate');
out.checks.dragInNavigate = devMarker('D1').opt.draggable;
out.messages.length = 0;
fire('mousemove', { lngLat: { lat: 1, lng: 2 } });
out.checks.noHoverInNavigate = !out.messages.some((m) => m.type === 'hover_load');
api.setMapMode('cable');
api.setMapMode('pdu'); out.messages.length = 0;
click(0, 0, 28100, 47000); click(0, 0, 28200, 47000);
out.checks.pduMsg = out.messages.filter((m) => m.type === 'net_pdu').map((m) => [m.a, m.b]);
api.setMapMode('device'); out.messages.length = 0; click(47.3, 28.3);
out.checks.deviceMsg = out.messages.map((m) => m.type);
api.setMapMode('navigate'); out.messages.length = 0;
click(0, 0, 28200 + 1, 47000);           // устройство D2
click(0, 0, 28050, 47000 + 3);           // линк L1 (между T1 и D1)
out.checks.netSelect = out.messages.map((m) => m.type + ':' + m.id);
const alive0 = markersAlive;
api.animate([[47.0, 28.0], [47.0, 28.1], [47.0, 28.2]], true, 'RTT 1.2 мс');
out.checks.animStarted = markersAlive - alive0;        // пакет
let tNow = performance.now();
while (frames.length) { const f = frames.shift(); tNow += 400; f(tNow); }
out.checks.animResult = markers.filter((m) => m.alive && String(m.el.className).startsWith('pkt-result')).map((m) => m.el.textContent);
api.animate([[47.0, 28.0]], false, 'нет маршрута');    // пакет не вышел — только метка ✗
out.checks.animFail = markers.filter((m) => m.alive && String(m.el.className).includes('fail')).map((m) => m.el.textContent);
api.setTraffic({ users: [[47.0, 28.05, 1], [47.0, 28.06, 3], [47.0, 28.07, 0]], lines: [[47.0, 28.05, 47.0, 28.0, 1]],
                 linkUtil: { L1: 0.9, L2: 0.0 } });
out.checks.trafficUsers = mapInst.getSource('trfusers').data.features.length;
out.checks.trafficLines = mapInst.getSource('trflines').data.features.length;
out.checks.flowDots = mapInst.getSource('flow').data.features.length > 0;
out.checks.linkRecolored = String(mapInst.getSource('netlinks').data.features[0].properties.col).startsWith('hsl(');
out.checks.labelPct = mapInst.getSource('netlinks').data.features.some((f) => f.properties.lbl.includes('90%'));
api.clearTraffic();
out.checks.trafficCleared = mapInst.getSource('trfusers').data.features.length === 0;
frames.length = 0;

// перетаскивание устройства: кабели тянутся следом сразу, до отпускания
api.setNetwork(JSON.parse(JSON.stringify(NET)));
api.setMapMode('navigate');
const d1 = markers.find((m) => m.alive && m.el.className === 'net-dev' && m.el.dataset.devId === 'D1');
d1.setLngLat([28.15, 47.05]); d1.h.drag();
const l1 = mapInst.getSource('netlinks').data.features.find((f) => f.properties.id === 'L1');
out.checks.liveCable = l1.geometry.coordinates[1][0] === 28.15 && l1.geometry.coordinates[1][1] === 47.05;
out.messages.length = 0; d1.h.dragend();
out.checks.moveMsg = out.messages.filter((m) => m.type === 'net_move').map((m) => m.id);

// двойной клик по устройству / вышке — SNMP (без приближения карты)
api.setNetwork(JSON.parse(JSON.stringify(NET)));
api.setTowers([T(1, 47.0, 28.0, rays, 360, 0, 5)]);
out.messages.length = 0;
let prevented = 0;
fire('dblclick', { point: { x: 28200, y: 47000 }, lngLat: { lat: 47, lng: 28.2 }, preventDefault() { prevented++; } });
fire('dblclick', { point: { x: 28000 + 2, y: 47000 }, lngLat: { lat: 47, lng: 28 }, preventDefault() { prevented++; } });
out.checks.snmpMsg = out.messages.filter((m) => m.type === 'snmp').map((m) => m.dev || ('tower:' + m.tower));
out.checks.snmpNoZoom = prevented;

// кэш лучей: Python прислал rays: null — карта берёт лучи из кэша
api.setTowers([Object.assign(T(1, 47.0, 28.0, null, 360, 0, 5))]);
out.checks.raysFromCache = mapInst.getSource('coverage').data.features.length;

// перенос вышки мышью
out.messages.length = 0;
const ev = (x, y, lat, lng) => ({ point: { x, y }, lngLat: { lat, lng }, preventDefault() {}, originalEvent: { button: 0 } });
fire('mousedown', ev(28000, 47000, 47.0, 28.0));
fire('mousemove', ev(28030, 47000, 47.0, 28.03));
const tp = mapInst.getSource('towers').data.features[0].geometry.coordinates;
out.checks.towerDragPreview = Math.abs(tp[0] - 28.03) < 1e-9;
const lt = mapInst.getSource('netlinks').data.features.find((f) => f.properties.id === 'L1');
out.checks.towerCableFollows = Math.abs(lt.geometry.coordinates[0][0] - 28.03) < 1e-9;
fire('mouseup', ev(28030, 47000, 47.0, 28.03));
fire('click', ev(28030, 47000, 47.0, 28.03));
out.checks.towerMoveMsg = out.messages.filter((m) => m.type === 'tower_move' || m.type === 'select')
  .map((m) => m.type + ':' + m.id + ':' + (m.lon !== undefined ? m.lon.toFixed(2) : ''));
api.setTowers([T(1, 47.0, 28.03, rays, 360, 0, 6)]);                   // ответ Python
out.checks.dragCleared = api.getState().towerDrag === null;
out.messages.length = 0;
fire('mousedown', ev(28030, 47000, 47.0, 28.03)); fire('mouseup', ev(28030, 47000, 47.0, 28.03));   // просто клик
out.checks.clickNoMove = out.messages.filter((m) => m.type === 'tower_move').length;
api.setNetwork(NET);
api.setTowers([T(1, 47.0, 28.0, rays, 360, 0), T(2, 47.1, 28.1, sector, 65, 90)]);   // вернуть вышки для проверки подложки
api.setMapMode('navigate');

// смена подложки: слои восстанавливаются, данные на месте
api.setBase('light');
out.checks.styleSwitch = mapInst.style.includes('positron') && !!mapInst.getLayer('coverage-fill') &&
  mapInst.getSource('coverage').data.features.length === 8;
api.setProbe(1, 2, '-70 дБм', '#0f0'); api.clearProbe();
console.log = () => {};
process.stdout.write(JSON.stringify(out), () => process.exit(0));   // таймеры анимации не должны держать процесс
