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
const makeEl = () => {
  const el = { style: {}, textContent: '', innerHTML: '', _cls: new Set(), _kids: {},
    setAttribute() {}, addEventListener() {},
    classList: { toggle(c, on) { on ? el._cls.add(c) : el._cls.delete(c); }, add(c) { el._cls.add(c); } },
    querySelector(sel) { return (el._kids[sel] = el._kids[sel] || makeEl()); } };
  return el;
};
const handlers = {}, docHandlers = {};
global.document = { getElementById: () => makeEl(), createElement: makeEl,
  body: makeEl(), addEventListener: (e, f) => { docHandlers[e] = f; } };
global.console = { log: (m) => { if (String(m).startsWith('PY:')) out.messages.push(JSON.parse(m.slice(3))); },
  warn() {}, error() {} };

let mapInst = null, markersAlive = 0;
const markers = [];
class Marker { constructor(o) { this.el = o.element; this.opt = o; this.h = {}; this.ll = null; this.alive = false; markers.push(this); }
  setLngLat(ll) { this.ll = ll; return this; } getLngLat() { return { lng: this.ll[0], lat: this.ll[1] }; }
  on(e, f) { this.h[e] = f; return this; }
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
  project(p) { return { x: p[0] * 1000, y: p[1] * 1000 }; }
  flyTo() {} fitBounds() {}
  setStyle(s) { this.style = s; this.sources = {}; this.layers = {}; handlers['style.load'](); }
}
global.maplibregl = { Map: MapMock, Marker, NavigationControl: class {}, ScaleControl: class {} };

vm.runInThisContext(res('map_app.js'));
const fire = (e, ev) => handlers[e](ev);
const click = (lat, lon, x = 0, y = 0) => fire('click', { lngLat: { lat, lng: lon }, point: { x, y } });

fire('style.load');
out.checks.ready = out.messages.some((m) => m.type === 'ready');
out.checks.layers = ['coverage-fill', 'measure-line', 'zones-fill', 'draft-pts', '3d-buildings-extrusion'].every((l) => mapInst.getLayer(l));

// вышка: круг по 4 зонам, 180 лучей
const rays = []; for (let i = 0; i < 180; i++) rays.push([i * 2, 50, 100, 150, 200]);
const sector = []; for (let i = 0; i < 33; i++) sector.push([(345 + i * 2) % 360, 50, 100, 150, 200]);   // сектор через 0°
const T = (id, lat, lon, r, bw, az) => ({ id, name: 'T' + id, lat, lon, height: 5, tilt: 0, scale: 1,
  color: [255, 0, 0, 255], azimuth: az, beamwidth: bw, model_b64: null, rays: r });
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
const ringE = mapInst.getSource('coverage').data.features[7].geometry.coordinates[0];
out.checks.previewEast = ringE.slice(1, -1).every((p) => p[0] > 28.1);
hnd.h.dragend();
out.checks.azMsg = out.messages.filter((m) => m.type === 'azimuth').map((m) => [m.id, m.azimuth]);
api.setTowers([T(1, 47.0, 28.0, rays, 360, 0), T(2, 47.1, 28.1, sector, 65, 90)]);   // ответ Python
out.checks.previewCleared = api.getState().azPreview === null;
api.setMapMode('measure');
out.checks.handleHiddenInMeasure = !liveHandle();
api.setMapMode('navigate');

// смена подложки: слои восстанавливаются, данные на месте
api.setBase('light');
out.checks.styleSwitch = mapInst.style.includes('positron') && !!mapInst.getLayer('coverage-fill') &&
  mapInst.getSource('coverage').data.features.length === 8;
api.setProbe(1, 2, '-70 дБм', '#0f0'); api.clearProbe();
console.log = () => {};
process.stdout.write(JSON.stringify(out));
