"""Оценка покрытия участка набором вышек (целевая функция для алгоритмов расстановки)."""
import math

import numpy as np

import geo
from calculations import M_PER_DEG_LAT

LEVEL_NAMES = ("none", "weak", "good", "excellent", "green")   # индекс = уровень 0..4


def level_at_points(tower, lats, lons):
    """Лучший уровень покрытия вышкой (0..4) в точках: 0 — нет, 4 — «зелёная» зона."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    dx = (lons - tower.lon) * M_PER_DEG_LAT * math.cos(math.radians(tower.lat))
    dy = (lats - tower.lat) * M_PER_DEG_LAT
    n = tower.reach.shape[0]
    idx = (np.floor(np.degrees(np.arctan2(dx, dy)) % 360.0 / (360.0 / n) + 0.5).astype(int)) % n
    d = np.hypot(dx, dy)
    return (d[:, None] <= tower.reach[idx]).sum(axis=1)


def sample_polygon(poly, forbidden=None, step_m=None, max_points=60000):
    """Равномерная сетка точек внутри polygon (минус запретные зоны): (lats, lons, step_m)."""
    lat_min, lat_max, lon_min, lon_max = geo.polygon_bounds(poly)
    lat_mid = (lat_min + lat_max) / 2.0
    area = max(geo.polygon_area_m2(poly), 1.0)
    if step_m is None:
        step_m = max(10.0, math.sqrt(area / max_points))
    dlat = step_m / M_PER_DEG_LAT
    dlon = step_m / (M_PER_DEG_LAT * math.cos(math.radians(lat_mid)))
    la, lo = np.meshgrid(np.arange(lat_min + dlat / 2, lat_max, dlat),
                         np.arange(lon_min + dlon / 2, lon_max, dlon), indexing="ij")
    la, lo = la.ravel(), lo.ravel()
    keep = geo.points_in_polygon(la, lo, poly)
    for fz in forbidden or []:
        keep &= ~geo.points_in_polygon(la, lo, fz)
    return la[keep], lo[keep], step_m


def coverage_stats(towers, poly, forbidden=None, step_m=None, max_points=60000):
    """Доли участка по уровням сигнала. None, если участок не задан."""
    if not poly or len(poly) < 3:
        return None
    la, lo, step = sample_polygon(poly, forbidden, step_m, max_points)
    if la.size == 0:
        return None
    best = np.zeros(la.shape, dtype=int)
    for t in towers:
        np.maximum(best, level_at_points(t, la, lo), out=best)
    counts = np.bincount(best, minlength=5) / la.size
    stats = {name: float(counts[i]) for i, name in enumerate(LEVEL_NAMES)}
    stats["covered"] = float(1.0 - counts[0])             # хотя бы «слабая» зона
    stats["good_or_better"] = float(counts[2:].sum())     # «средняя» и лучше
    stats["area_km2"] = float(geo.polygon_area_m2(poly) / 1e6)
    stats["points"] = int(la.size)
    stats["step_m"] = float(step)
    return stats
