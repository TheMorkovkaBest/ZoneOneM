"""Замер скорости расчёта вышки на синтетическом городе.  python tools/benchmark.py"""
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from buildings import BuildingData          # noqa: E402
from tower import Tower                     # noqa: E402

LAT0, LON0 = 47.0101, 28.8638
M_LAT = 111320.0
M_LON = M_LAT * math.cos(math.radians(LAT0))


def synthetic_city(half_km=1.0, seed=1):
    rnd = random.Random(seed)
    out, step = [], 60.0
    n = int(half_km * 1000 / step)
    k = 0
    for i in range(-n, n):
        for j in range(-n, n):
            if rnd.random() < 0.25:
                continue
            w, h = rnd.uniform(15, 40), rnd.uniform(15, 40)
            cx, cy = i * step + rnd.uniform(-5, 5), j * step + rnd.uniform(-5, 5)
            pts = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                   (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]
            out.append({'id': f"s{k}", 'height': float(rnd.choice([6, 9, 12, 15, 15, 18, 27, 36])),
                        'poly': [(LAT0 + y / M_LAT, LON0 + x / M_LON) for x, y in pts]})
            k += 1
    return out


def main():
    bd = BuildingData()
    t0 = time.perf_counter()
    bd.add_buildings(synthetic_city())
    print(f"зданий: {len(bd.buildings)}, растеризация (один раз): {(time.perf_counter() - t0) * 1000:.0f} мс")
    for name, kw in [("микросота 5м/1800", dict(freq=1800, power=10, height=5)),
                     ("макросота 35м/800", dict(freq=800, power=40, height=35))]:
        for label, data in (("с учётом зданий", bd), ("без зданий", None)):
            ts = []
            for k in range(30):
                t = time.perf_counter()
                Tower("t", LAT0 + k * 1e-4, LON0 + k * 1e-4, tilt=6, wall_loss=20, color=[255, 0, 0, 255],
                      building_data=data, **kw)
                ts.append((time.perf_counter() - t) * 1000)
            print(f"НОВЫЙ {name}, {label}: медиана {statistics.median(ts):.1f} мс (макс {max(ts):.1f})")


if __name__ == "__main__":
    main()
