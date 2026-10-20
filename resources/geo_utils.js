/* Чистые функции без DOM: геодезия и построение полигонов покрытия.
   Дублируют geo.py (проверяется в tests/test_js.js). */
(function (root) {
  'use strict';
  const R = 6371008.8, M_LAT = 111320.0;
  const rad = (d) => d * Math.PI / 180, deg = (r) => r * 180 / Math.PI;

  function haversine(lat1, lon1, lat2, lon2) {
    const p1 = rad(lat1), p2 = rad(lat2), dp = p2 - p1, dl = rad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  function bearing(lat1, lon1, lat2, lon2) {
    const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
    const y = Math.sin(dl) * Math.cos(p2);
    const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return (deg(Math.atan2(y, x)) + 360) % 360;
  }
  function fmtDist(m) { return m < 1000 ? Math.round(m) + ' м' : (m / 1000).toFixed(3) + ' км'; }
  const COMPASS = ['С', 'СВ', 'В', 'ЮВ', 'Ю', 'ЮЗ', 'З', 'СЗ'];
  function fmtBearing(d) { return d.toFixed(1) + '° ' + COMPASS[Math.floor((d + 22.5) / 45) % 8]; }

  function angleOffset(angle, azimuth) { return (angle - azimuth + 540) % 360 - 180; }

  /* Кольцо полигона зоны (col: 1=green 2=excellent 3=good 4=weak) в формате [lon, lat].
     t.rays = [[angle, green, exc, good, weak], ...] — только лучи внутри сектора. */
  function zoneRing(t, col) {
    if (!t.rays || t.rays.length === 0) return null;
    const mLon = M_LAT * Math.cos(rad(t.lat));
    const rays = t.rays.slice();
    const sector = t.beamwidth < 360 && !t.full;     // full — есть лучи по всему кругу (задний лепесток)
    rays.sort((a, b) => angleOffset(a[0], t.azimuth) - angleOffset(b[0], t.azimuth));
    const ring = [];
    if (sector) ring.push([t.lon, t.lat]);
    for (const r of rays) {
      const a = rad(r[0]), d = r[col];
      ring.push([t.lon + Math.sin(a) * d / mLon, t.lat + Math.cos(a) * d / M_LAT]);
    }
    ring.push(sector ? [t.lon, t.lat] : ring[0].slice());
    return ring.length >= 4 ? ring : null;
  }

  const api = { haversine, bearing, fmtDist, fmtBearing, zoneRing, angleOffset };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.GeoUtils = api;
})(typeof window !== 'undefined' ? window : globalThis);
