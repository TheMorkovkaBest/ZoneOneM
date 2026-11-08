"""Алгоритм 2: ИИ-расстановка вышек (нейросеть-суррогат + точная проверка).

Идея: обученная сеть (ai_model.SurrogateNet) за один проход строит карту
ожидаемого прироста покрытия по всему участку. Из неё берутся K лучших
разнесённых клеток, и только они проверяются точным расчётом вышки (Tower +
уровни покрытия, как в coverage.coverage_stats). Ставится лучшая — и так до
target_coverage_ratio или max_towers.

Здесь же живут общие для обучения и программы части:
  * build_features — растр признаков участка (одинаковый при обучении и работе);
  * CoverageEvaluator — быстрый точный расчёт доли покрытия;
  * make_tower — вышка из task.tower_type с установкой на крышу.
"""
import math
import os
import time

import numpy as np

import c_coverage
import coverage
import geo
from calculations import M_PER_DEG_LAT
from tower import Tower

GRID_N = 64                   # растр признаков GRID_N × GRID_N клеток
N_CHANNELS = 10
ROOF_MIN_H = 3.0              # ниже — не крыша, а навес/гараж
MAST_ON_ROOF_M = 5.0          # мачта на кровле, как в Алгоритме 1 и GUI
DENSITY_RADIUS_M = 200.0
TOP_K = 8                     # сколько клеток сети проверять точным расчётом
SITE_K = 6                    # и сколько крыш-кандидатов (с самым высоким баллом сети)
NMS_RADIUS_M = 80.0           # кандидаты не ближе этого друг к другу
MIN_SITE_DIST_M = 60.0        # не ставить новую площадку вплотную к уже занятой

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "models", "ai_surrogate.npz")

# уровень из coverage.level_at_points: 0 нет, 1 слабый, 2 средний, 3 отличный, 4 зелёный
QUALITY_LEVEL = {"weak": 1, "good": 2, "excellent": 3, "green": 4}


# ---------------------------------------------------------------------------
#  Вышка и точная оценка покрытия
# ---------------------------------------------------------------------------
def roof_height(building_data, lat, lon):
    if building_data is None:
        return 0.0
    try:
        return float(building_data.heights_at([lat], [lon])[0])
    except Exception:
        return 0.0


def make_tower(task, lat, lon, azimuth=0.0, name="ai", bld_h=None):
    """Вышка типа task.tower_type в точке; на крыше — высота здания + мачта (как ставит GUI)."""
    tt = dict(task.tower_type or {})
    if bld_h is None:
        bld_h = roof_height(task.building_data, lat, lon)
    height = bld_h + MAST_ON_ROOF_M if bld_h > 0.0 else float(tt.get("height", 30.0))
    return Tower(name, lat, lon,
                 freq=float(tt.get("freq", 1800.0)), power=float(tt.get("power", 20.0)),
                 height=height, tilt=float(tt.get("tilt", 4.0)),
                 wall_loss=float(tt.get("wall_loss", 20.0)), color=[255, 0, 0, 255],
                 azimuth=float(azimuth), beamwidth=float(tt.get("beamwidth", 360.0)),
                 building_data=task.building_data)


def azimuths_for(task):
    """Азимуты для перебора: круговая антенна — один, секторная — с шагом task.azimuth_step_deg."""
    bw = float((task.tower_type or {}).get("beamwidth", 360.0))
    if bw >= 330.0:
        return [0.0]
    step = max(15.0, float(getattr(task, "azimuth_step_deg", 45.0)))
    return list(np.arange(0.0, 360.0, step))


class CoverageEvaluator:
    """Точная доля покрытия участка — та же сетка и уровни, что в coverage.coverage_stats,
    но точки участка считаются один раз, а не при каждом вызове."""

    def __init__(self, task, max_points=25000):
        self.req = QUALITY_LEVEL.get(getattr(task, "min_quality_level", "good"), 2)
        self._reach_col = {4: 0, 3: 1, 2: 2, 1: 3}.get(self.req, 2)
        self.lats, self.lons, self.step_m = coverage.sample_polygon(
            task.area, task.forbidden, step_m=getattr(task, "grid_step_m", None), max_points=max_points)
        self.best = np.zeros(self.lats.shape, dtype=int)
        self._c_available = c_coverage.is_c_available()
        num_words = (self.n_points + 63) // 64
        self.current_mask_u64 = np.zeros(num_words, dtype=np.uint64)

    @property
    def n_points(self):
        return int(self.lats.size)

    def ratio(self, best=None):
        if best is not None:
            if isinstance(best, np.ndarray) and best.dtype == np.uint64:
                return float(np.bitwise_count(best).sum()) / self.n_points if self.n_points else 0.0
            return float((best >= self.req).mean()) if best.size else 0.0
        if self._c_available and self.n_points > 0:
            return float(np.bitwise_count(self.current_mask_u64).sum()) / self.n_points
        return float((self.best >= self.req).mean()) if self.best.size else 0.0

    def try_tower(self, tower):
        """Доля покрытия, если добавить вышку, и новый массив уровней/маски (состояние не меняется)."""
        if self._c_available and tower.reach.ndim == 2 and tower.reach.shape[1] >= 4 and self.n_points > 0:
            reach_1d = tower.reach[:, self._reach_col]
            mask_u64, _ = c_coverage.c_compute_sector_mask(
                self.lats, self.lons, tower.lat, tower.lon, reach_1d
            )
            combined = self.current_mask_u64 | mask_u64
            ratio = float(np.bitwise_count(combined).sum()) / self.n_points
            return ratio, combined
        best = np.maximum(self.best, coverage.level_at_points(tower, self.lats, self.lons))
        return self.ratio(best), best

    def add(self, best):
        if isinstance(best, np.ndarray) and best.dtype == np.uint64:
            self.current_mask_u64 = best
        else:
            self.best = best


# ---------------------------------------------------------------------------
#  Признаки
# ---------------------------------------------------------------------------
class Grid:
    """Квадратная сетка GRID_N × GRID_N вокруг участка: клетка (r, c) ↔ (lat, lon)."""

    def __init__(self, area, n=GRID_N):
        lat_min, lat_max, lon_min, lon_max = geo.polygon_bounds(area)
        self.n = n
        self.lat0 = (lat_min + lat_max) / 2.0
        self.lon0 = (lon_min + lon_max) / 2.0
        self.m_lon = M_PER_DEG_LAT * math.cos(math.radians(self.lat0))
        span = max((lat_max - lat_min) * M_PER_DEG_LAT, (lon_max - lon_min) * self.m_lon, 50.0)
        self.step_m = span * 1.02 / n
        off = (np.arange(n) + 0.5 - n / 2.0) * self.step_m
        la, lo = np.meshgrid(self.lat0 + off / M_PER_DEG_LAT, self.lon0 + off / self.m_lon, indexing="ij")
        self.lats, self.lons = la, lo

    def cell_of(self, lat, lon):
        r = int((lat - self.lat0) * M_PER_DEG_LAT / self.step_m + self.n / 2.0)
        c = int((lon - self.lon0) * self.m_lon / self.step_m + self.n / 2.0)
        return min(max(r, 0), self.n - 1), min(max(c, 0), self.n - 1)

    def disk(self, r, c, radius_m):
        rr = max(1, int(math.ceil(radius_m / self.step_m)))
        y, x = np.ogrid[:self.n, :self.n]
        return (y - r) ** 2 + (x - c) ** 2 <= rr * rr


def _box_blur(img, radius):
    """Среднее по квадрату (2r+1)² через кумулятивные суммы."""
    if radius < 1:
        return img.astype(np.float32)
    p = np.pad(img.astype(np.float64), radius + 1, mode="edge")
    s = p.cumsum(0).cumsum(1)
    k = 2 * radius + 1
    out = s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]
    return (out[:img.shape[0], :img.shape[1]] / (k * k)).astype(np.float32)


def static_features(task, grid):
    """Неизменная часть признаков (всё, кроме текущего покрытия) и маска допустимых клеток."""
    n = grid.n
    flat_la, flat_lo = grid.lats.ravel(), grid.lons.ravel()
    inside = geo.points_in_polygon(flat_la, flat_lo, task.area).reshape(n, n)
    forb = np.zeros((n, n), dtype=bool)
    for fz in task.forbidden or []:
        forb |= geo.points_in_polygon(flat_la, flat_lo, fz).reshape(n, n)
    heights = np.zeros((n, n), dtype=np.float32)
    if task.building_data is not None:
        heights = np.asarray(task.building_data.heights_at(flat_la, flat_lo), dtype=np.float32).reshape(n, n)
    allowed = inside & ~forb
    roof = (heights >= ROOF_MIN_H) & allowed
    tt = task.tower_type or {}
    X = np.zeros((N_CHANNELS, n, n), dtype=np.float32)
    X[0] = inside
    X[1] = forb
    X[2] = np.clip(heights / 50.0, 0.0, 2.0)
    X[3] = roof
    # X[4] — текущее покрытие, заполняется в build_features
    X[5] = _box_blur(heights > 0, int(round(DENSITY_RADIUS_M / grid.step_m)))
    X[6] = float(tt.get("power", 20.0)) / 40.0
    X[7] = float(tt.get("height", 30.0)) / 50.0
    X[8] = float(tt.get("freq", 1800.0)) / 3000.0
    X[9] = grid.step_m / 50.0
    return X, allowed


def coverage_channel(grid, towers):
    """Лучший уровень сигнала (0..4)/4 в клетках сетки от уже поставленных вышек."""
    best = np.zeros(grid.n * grid.n, dtype=int)
    for t in towers:
        np.maximum(best, coverage.level_at_points(t, grid.lats.ravel(), grid.lons.ravel()), out=best)
    return (best / 4.0).reshape(grid.n, grid.n).astype(np.float32)


def build_features(task, placed_towers, grid=None, static=None):
    """Растр признаков [N_CHANNELS, GRID_N, GRID_N], сетка и маска допустимых клеток."""
    grid = grid or Grid(task.area)
    if static is None:
        static = static_features(task, grid)
    X0, allowed = static
    X = X0.copy()
    X[4] = coverage_channel(grid, placed_towers)
    return X, grid, allowed


# ---------------------------------------------------------------------------
#  Модель
# ---------------------------------------------------------------------------
_MODEL = None
_MODEL_MTIME = None


def load_model(path=MODEL_PATH):
    """Загружает веса один раз (перезагружает, если файл обновился). None — модели нет."""
    global _MODEL, _MODEL_MTIME
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    if _MODEL is None or mtime != _MODEL_MTIME:
        from ai_model import SurrogateNet
        _MODEL, _MODEL_MTIME = SurrogateNet.load(path), mtime
    return _MODEL


def pick_candidates(score, grid, k=TOP_K, nms_m=NMS_RADIUS_M):
    """K лучших клеток карты с подавлением соседей (чтобы не взять 8 точек на одной крыше)."""
    s = score.copy()
    out = []
    while len(out) < k and (s > -np.inf).any():
        r, c = np.unravel_index(int(np.argmax(s)), s.shape)
        out.append((r, c))
        s[grid.disk(r, c, nms_m)] = -np.inf
    return out


# ---------------------------------------------------------------------------
#  Расстановка
# ---------------------------------------------------------------------------
def ai_place(task, model, top_k=TOP_K):
    """Пошаговая расстановка: сеть предлагает top_k точек, точный расчёт выбирает лучшую."""
    progress = getattr(task, "progress_callback", None)
    cancelled = getattr(task, "is_cancelled", None)
    if not task.area or len(task.area) < 3:
        return []
    ev = CoverageEvaluator(task)
    if ev.n_points == 0:
        return []
    grid = Grid(task.area)
    static = static_features(task, grid)
    allowed = static[1]
    if not allowed.any():
        return []
    target = float(getattr(task, "target_coverage_ratio", 0.95))
    min_gain = max(1, int(getattr(task, "min_gain_tiles", 3))) / ev.n_points
    max_per_site = max(1, int(getattr(task, "max_sectors_per_site", 3)))
    azimuths = azimuths_for(task)
    quality = getattr(task, "min_quality_level", "good")
    # точки на крышах высоких зданий (как в Алгоритме 1): сеть их только ранжирует
    import algorithm_1
    sites = algorithm_1.find_candidate_sites(task.area, task.forbidden, task.building_data,
                                             float((task.tower_type or {}).get("height", 30.0)),
                                             cluster_size_m=max(150.0, grid.step_m * 8))
    sites = [(s.lat, s.lon) + grid.cell_of(s.lat, s.lon) for s in sites if s.building_id is not None]

    placed, result = [], []
    site_use = {}                    # клетка → сколько секторов на ней уже стоит
    cur = 0.0
    t0 = time.perf_counter()
    while len(placed) < int(task.max_towers) and cur < target:
        if cancelled and cancelled():
            return result
        X, _, _ = build_features(task, placed, grid, static)
        score = model.predict(X).astype(np.float64)
        score[~allowed] = -np.inf
        for (r, c), used in site_use.items():
            if used >= max_per_site:
                score[grid.disk(r, c, MIN_SITE_DIST_M)] = -np.inf
            else:                    # на этой площадке можно добавить ещё сектор
                score[grid.disk(r, c, MIN_SITE_DIST_M)] = -np.inf
                score[r, c] = np.inf
        cands = [(float(grid.lats[r, c]), float(grid.lons[r, c]), r, c)
                 for r, c in pick_candidates(score, grid, top_k)]
        roofs = sorted((s for s in sites if score[s[2], s[3]] > -np.inf), key=lambda s: -score[s[2], s[3]])
        cands += roofs[:SITE_K]
        if not cands:
            break

        best = None
        for lat, lon, r, c in cands:
            bld_h = roof_height(task.building_data, lat, lon)
            for az in azimuths:
                t = make_tower(task, lat, lon, az, name=f"ai{len(placed) + 1}", bld_h=bld_h)
                val, lv = ev.try_tower(t)
                if best is None or val > best[0]:
                    best = (val, lv, t, r, c, az)
        val, lv, t, r, c, az = best
        if val - cur < min_gain:
            break
        ev.add(lv)
        placed.append(t)
        site_use[(r, c)] = site_use.get((r, c), 0) + 1
        result.append({
            "lat": t.lat, "lon": t.lon, "height": t.height, "azimuth": float(az),
            "beamwidth": t.beamwidth, "building_id": None,
            "site_name": f"ИИ-площадка {len(placed)}",
            "gain_tiles": int(round((val - cur) * ev.n_points)), "quality_level": quality,
        })
        cur = val
        if progress:
            n = len(placed)
            eta = (time.perf_counter() - t0) / n * (int(task.max_towers) - n)
            progress(min(99.0, 100.0 * max(cur / max(target, 1e-9), n / max(int(task.max_towers), 1))),
                     f"ИИ: вышка {n}, покрытие {cur:.0%}", eta)
    return result
