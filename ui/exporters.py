"""Экспорт данных в форматы, удобные инженеру: CSV, GeoJSON (QGIS/Google Earth), JSON-проект."""
import csv
import io
import json
import math

from calculations import NUM_RAYS, ZONE_NAMES, M_PER_DEG_LAT, angle_offset, visible_rays

CSV_COLUMNS = ["name", "type_name", "lat", "lon", "freq", "power", "height", "tilt",
               "wall_loss", "azimuth", "beamwidth", "r_green", "r_exc", "r_good", "r_weak"]


def towers_to_csv(towers):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for t in towers:
        d = t.to_dict()
        w.writerow([d.get(c, "") if d.get(c) is not None else "" for c in CSV_COLUMNS])
    return buf.getvalue()


def _ring(t, z):
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(t.lat))
    pts = []
    show = visible_rays(t.angles, t.reach, t.azimuth, t.beamwidth)
    idx = sorted((i for i in range(len(t.angles)) if show[i]), key=lambda i: angle_offset(t.angles[i], t.azimuth))
    for i in idx:                                  # по порядку от левого края сектора к правому
        r = float(t.reach[i, z])
        rad = math.radians(t.angles[i])
        pts.append([t.lon + math.sin(rad) * r / m_lon, t.lat + math.cos(rad) * r / M_PER_DEG_LAT])
    if not pts:
        return None
    if t.beamwidth < 360.0 and len(idx) < NUM_RAYS:     # сектор без заднего лепестка — клин из центра
        pts = [[t.lon, t.lat]] + pts + [[t.lon, t.lat]]
    else:
        pts.append(pts[0])
    return pts


def coverage_geojson(towers, algo_zone=None, forbidden=None):
    """FeatureCollection: вышки (Point), зоны покрытия (Polygon) и зоны проекта."""
    feats = []
    for t in towers:
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [t.lon, t.lat]},
                      "properties": {"kind": "tower", "name": t.name, "freq_mhz": t.freq,
                                     "power_w": t.power, "height_m": t.height}})
        for z in range(len(ZONE_NAMES) - 1, -1, -1):       # от большей зоны к меньшей
            ring = _ring(t, z)
            if ring and len(ring) >= 4:
                feats.append({"type": "Feature",
                              "geometry": {"type": "Polygon", "coordinates": [ring]},
                              "properties": {"kind": "coverage", "tower": t.name, "zone": ZONE_NAMES[z]}})
    if algo_zone:
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon", "coordinates": [_closed(algo_zone)]},
                      "properties": {"kind": "algo_zone"}})
    for fz in forbidden or []:
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon", "coordinates": [_closed(fz)]},
                      "properties": {"kind": "forbidden_zone"}})
    return {"type": "FeatureCollection", "features": feats}


def _closed(poly):
    ring = [[p[1], p[0]] for p in poly]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def project_to_json(types, towers, algo_zone, forbidden, network=None):
    return json.dumps({
        "format": "zonem-project", "version": 2, "types": types,
        "towers": [t.to_dict() for t in towers],
        "zones": {"algo": algo_zone, "forbidden": forbidden},
        "network": network,
    }, ensure_ascii=False, indent=1)


def project_from_json(text):
    data = json.loads(text)
    if data.get("format") != "zonem-project":
        raise ValueError("Это не файл проекта ZoneM")
    return data
