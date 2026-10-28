"""Геодезические и геометрические утилиты.

Используются и GUI (линейка, зоны), и расчётным ядром, и будущими алгоритмами
расстановки. Полигоны везде задаются списком ``[(lat, lon), ...]``.
"""
import math
import re

import numpy as np

EARTH_RADIUS_M = 6371008.8
# То же значение, что в calculations.py: плоская модель «метров на градус».
M_PER_DEG_LAT = 111320.0

_COMPASS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def haversine_m(lat1, lon1, lat2, lon2):
    """Расстояние по большому кругу, метры."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Начальный азимут от точки 1 к точке 2, 0..360° (от севера по часовой)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat, lon, bearing, dist_m):
    """Точка на расстоянии dist_m по азимуту bearing от (lat, lon)."""
    d = dist_m / EARTH_RADIUS_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def polyline_length_m(points):
    """Длина ломаной [(lat, lon), ...] в метрах."""
    return sum(haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))


def local_xy(lat0, lon0, lat, lon):
    """Плоские координаты (восток, север) в метрах относительно (lat0, lon0)."""
    x = (np.asarray(lon, dtype=float) - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0))
    y = (np.asarray(lat, dtype=float) - lat0) * M_PER_DEG_LAT
    return x, y


def polygon_area_m2(poly):
    """Площадь полигона, м² (формула шнурования в локальной проекции)."""
    if len(poly) < 3:
        return 0.0
    lat0 = sum(p[0] for p in poly) / len(poly)
    lon0 = sum(p[1] for p in poly) / len(poly)
    xs, ys = local_xy(lat0, lon0, [p[0] for p in poly], [p[1] for p in poly])
    return float(abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))) / 2.0)


def polygon_bounds(poly):
    """(lat_min, lat_max, lon_min, lon_max)."""
    lats = [p[0] for p in poly]
    lons = [p[1] for p in poly]
    return min(lats), max(lats), min(lons), max(lons)


def points_in_polygon(lats, lons, poly):
    """Векторная проверка «точка внутри полигона» (чёт-нечет). Возвращает bool-массив."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    inside = np.zeros(lats.shape, dtype=bool)
    n = len(poly)
    if n < 3:
        return inside
    for i in range(n):
        y1, x1 = poly[i]
        y2, x2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        cond = (y1 > lats) != (y2 > lats)
        x_cross = x1 + (lats - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (lons < x_cross)
    return inside


def point_in_polygon(lat, lon, poly):
    return bool(points_in_polygon(np.array([lat]), np.array([lon]), poly)[0])


def fspl_db(dist_m, freq_mhz):
    """Потери в свободном пространстве, дБ."""
    d_km = max(dist_m, 1e-3) / 1000.0
    return 20.0 * math.log10(d_km) + 20.0 * math.log10(freq_mhz) + 32.44


def dbm_to_w(dbm):
    return 10.0 ** ((dbm - 30.0) / 10.0)


def w_to_dbm(watts):
    return 10.0 * math.log10(max(watts, 1e-30)) + 30.0


def format_distance(m):
    return f"{m:.0f} м" if m < 1000.0 else f"{m / 1000.0:.3f} км"


def format_bearing(deg):
    return f"{deg:.1f}° ({_COMPASS[int((deg + 22.5) // 45) % 8]})"


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_latlon(text):
    """Разбирает «47.0101, 28.8638» / «47.0101 28.8638» / «47.0101;28.8638».

    Возвращает (lat, lon) или бросает ValueError.
    """
    nums = _NUM_RE.findall(text.replace(",", " ").replace(";", " "))
    if len(nums) != 2:
        raise ValueError("Нужны два числа: широта и долгота")
    lat, lon = float(nums[0]), float(nums[1])
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("Широта должна быть в диапазоне -90..90, долгота -180..180")
    return lat, lon
