"""Генерация обучающих примеров для Алгоритма 2 (ИИ-расстановка).

Каждый пример — случайный участок в синтетическом городе с 0..max_towers−1 уже
поставленными вышками. Для M случайных клеток честно считается, на сколько вырастет
доля покрытия, если поставить туда ещё одну вышку (Tower + те же уровни, что в
coverage.coverage_stats). Это и есть «правильные ответы» для нейросети.

Запуск (из корня проекта):
    python tools/make_dataset.py --scenes 300 --out models/dataset_small.npz      # проверка, ~минуты
    python tools/make_dataset.py --scenes 5000 --workers 6                         # полноценный, ~часы
"""
import argparse
import math
import os
import random
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# все папки проекта с .py-файлами — в sys.path (модули проекта импортируются «плоско»: import coverage)
_SKIP = {".venv", "venv", "env", ".git", ".idea", "__pycache__", "node_modules", "build", "dist", "site-packages"}
for _dir, _subs, _files in os.walk(_ROOT):
    _subs[:] = [s for s in _subs if s not in _SKIP and not s.startswith(".")]
    if _dir == _ROOT or any(f.endswith(".py") for f in _files):
        if _dir not in sys.path:
            sys.path.insert(0, _dir)

import numpy as np                                   # noqa: E402

import ai_placement as ai                            # noqa: E402
from auto_placement import PlacementTask             # noqa: E402
from buildings import BuildingData                   # noqa: E402
from calculations import M_PER_DEG_LAT               # noqa: E402
from tower import DEFAULT_PRESETS                    # noqa: E402

LABEL_CELLS = 48                  # сколько клеток размечать в одном примере
LABEL_AZ_STEP = 90.0              # для секторных антенн метка = лучшее из 4 азимутов


# ---------------------------------------------------------------------------
#  Случайный участок
# ---------------------------------------------------------------------------
def synthetic_city(rnd, lat0, lon0, half_m):
    """Здания [{'id','height','poly'}] в квадрате ±half_m вокруг (lat0, lon0)."""
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    step = rnd.uniform(35.0, 90.0)
    skip = rnd.uniform(0.15, 0.7)
    style = rnd.choice(["low", "mixed", "high"])
    hs = {"low": [3, 6, 6, 9, 9, 12], "mixed": [6, 9, 12, 15, 18, 27, 36],
          "high": [9, 15, 27, 36, 45, 60]}[style]
    out, k = [], 0
    n = int(half_m / step)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if rnd.random() < skip:
                continue
            w, h = rnd.uniform(12, step * 0.8), rnd.uniform(12, step * 0.8)
            cx, cy = i * step + rnd.uniform(-5, 5), j * step + rnd.uniform(-5, 5)
            pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                   (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
            out.append({"id": f"s{k}", "height": float(rnd.choice(hs)),
                        "poly": [(lat0 + y / M_PER_DEG_LAT, lon0 + x / m_lon) for x, y in pts]})
            k += 1
    return out


def random_polygon(rnd, lat0, lon0, r_m, n_min=5, n_max=10):
    """Звёздчатый многоугольник радиусом ~r_m (бывает невыпуклым)."""
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    n = rnd.randint(n_min, n_max)
    angs = sorted(rnd.uniform(0, 2 * math.pi) for _ in range(n))
    pts = []
    for a in angs:
        r = r_m * rnd.uniform(0.55, 1.0)
        pts.append((lat0 + r * math.sin(a) / M_PER_DEG_LAT, lon0 + r * math.cos(a) / m_lon))
    return pts


def random_task(rnd):
    """Случайная задача: город, участок 0.2–3 км², 0–2 запретные зоны, случайный тип вышки."""
    lat0 = rnd.uniform(40.0, 60.0)
    lon0 = rnd.uniform(20.0, 40.0)
    r_m = rnd.uniform(250.0, 1000.0)
    bd = BuildingData()
    bd.add_buildings(synthetic_city(rnd, lat0, lon0, r_m * 1.3))
    area = random_polygon(rnd, lat0, lon0, r_m)
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    forbidden = []
    for _ in range(rnd.choice([0, 0, 1, 2])):
        a, d = rnd.uniform(0, 2 * math.pi), rnd.uniform(0, r_m * 0.5)
        forbidden.append(random_polygon(rnd, lat0 + d * math.sin(a) / M_PER_DEG_LAT,
                                        lon0 + d * math.cos(a) / m_lon, r_m * rnd.uniform(0.1, 0.3), 4, 6))
    preset = dict(DEFAULT_PRESETS[rnd.choice(list(DEFAULT_PRESETS))])
    preset["power"] = float(preset["power"]) * rnd.uniform(0.5, 1.5)
    tt = {k: preset[k] for k in ("freq", "power", "height", "tilt", "wall_loss", "azimuth", "beamwidth")}
    return PlacementTask(area=area, forbidden=forbidden, tower_type=tt,
                         max_towers=rnd.randint(3, 12), building_data=bd,
                         min_quality_level=rnd.choice(["good", "good", "good", "weak"]),
                         grid_step_m=20.0, azimuth_step_deg=LABEL_AZ_STEP)


# ---------------------------------------------------------------------------
#  Один пример
# ---------------------------------------------------------------------------
def sample_cells(rnd, allowed, roof, m):
    """m клеток: ~¾ на крышах (там чаще ставят вышки), остальные — любые допустимые."""
    roof_idx = np.flatnonzero(roof & allowed)
    all_idx = np.flatnonzero(allowed)
    n_roof = min(len(roof_idx), int(m * 0.75))
    pick = list(rnd.sample(list(roof_idx), n_roof)) if n_roof else []
    rest = [i for i in all_idx if i not in set(pick)]
    pick += rnd.sample(rest, min(len(rest), m - len(pick)))
    return pick


def make_example(seed):
    """(X, Y, MASK) или None, если участок вышел пустым."""
    rnd = random.Random(seed)
    task = random_task(rnd)
    ev = ai.CoverageEvaluator(task)
    if ev.n_points == 0:
        return None
    grid = ai.Grid(task.area)
    static = ai.static_features(task, grid)
    allowed = static[1]
    if allowed.sum() < 10:
        return None
    roof = static[0][3] > 0

    # «история»: уже поставленные вышки — случайные допустимые клетки
    placed = []
    k = rnd.randint(0, task.max_towers - 1)
    for idx in sample_cells(rnd, allowed.ravel(), roof.ravel(), k):
        r, c = divmod(int(idx), grid.n)
        t = ai.make_tower(task, float(grid.lats[r, c]), float(grid.lons[r, c]),
                          rnd.choice(ai.azimuths_for(task)))
        placed.append(t)
        ev.add(ev.try_tower(t)[1])
    base = ev.ratio()

    X, _, _ = ai.build_features(task, placed, grid, static)
    Y = np.zeros((grid.n, grid.n), dtype=np.float32)
    MASK = np.zeros((grid.n, grid.n), dtype=np.uint8)
    for idx in sample_cells(rnd, allowed.ravel(), roof.ravel(), LABEL_CELLS):
        r, c = divmod(int(idx), grid.n)
        lat, lon = float(grid.lats[r, c]), float(grid.lons[r, c])
        gain = max(ev.try_tower(ai.make_tower(task, lat, lon, az))[0] for az in ai.azimuths_for(task)) - base
        Y[r, c] = gain
        MASK[r, c] = 1
    return X.astype(np.float16), Y, MASK


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenes", type=int, default=2000, help="сколько участков сгенерировать")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help="сколько процессов (ядер) использовать")
    ap.add_argument("--seed", type=int, default=0, help="начальное зерно (разные зёрна → разные данные)")
    ap.add_argument("--out", default=os.path.join(_ROOT, "models", "dataset.npz"))
    a = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    seeds = [a.seed * 1_000_003 + i for i in range(a.scenes)]
    Xs, Ys, Ms = [], [], []
    t0 = time.perf_counter()

    def collect(res, i):
        if res is not None:
            Xs.append(res[0]); Ys.append(res[1]); Ms.append(res[2])
        if (i + 1) % 10 == 0 or i + 1 == a.scenes:
            el = time.perf_counter() - t0
            eta = el / (i + 1) * (a.scenes - i - 1)
            print(f"  {i + 1}/{a.scenes} участков, {el / 60:.1f} мин, осталось ~{eta / 60:.1f} мин", flush=True)

    print(f"Генерация {a.scenes} участков на {a.workers} процессах…", flush=True)
    if a.workers > 1:
        from multiprocessing import Pool
        with Pool(a.workers) as pool:
            for i, res in enumerate(pool.imap_unordered(make_example, seeds, chunksize=2)):
                collect(res, i)
    else:
        for i, s in enumerate(seeds):
            collect(make_example(s), i)

    np.savez_compressed(a.out, X=np.stack(Xs), Y=np.stack(Ys), MASK=np.stack(Ms))
    print(f"Готово: {len(Xs)} примеров → {a.out}")


if __name__ == "__main__":
    main()
