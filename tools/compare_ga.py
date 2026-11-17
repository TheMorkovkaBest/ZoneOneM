"""Сравнение Алгоритма 3 (генетический) с Алгоритмами 1 и 2 на новых случайных участках.

    python tools/compare_ga.py --scenes 10

Покрытие для всех считается одинаково: coverage.coverage_stats.
«ГА» — чистый генетический алгоритм (без затравки), «ГА+А1» — с результатом
Алгоритма 1 в начальной популяции (так он запускается из программы).
"""
import argparse
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
import algorithm_1                                   # noqa: E402
import ga_placement as ga                            # noqa: E402
import make_dataset as md                            # noqa: E402
from compare_ai import score                         # noqa: E402


def timed(f):
    t0 = time.perf_counter()
    r = f()
    return r, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=10)
    ap.add_argument("--seed", type=int, default=777)
    a = ap.parse_args()
    model = ai.load_model()
    rnd = random.Random(a.seed)
    algos = [("Алг.1", lambda t: algorithm_1.optimize_coverage(t))]
    if model is not None:
        algos.append(("Алг.2 (ИИ)", lambda t: ai.ai_place(t, model)))
    algos.append(("ГА", lambda t: ga.ga_place(t, ga.GAParams(seed=1, seed_algorithm_1=False))))
    algos.append(("ГА+А1", lambda t: ga.ga_place(t, ga.GAParams(seed=1))))
    print("№   " + " | ".join(f"{n:>10}: покр. вышек   с" for n, _ in algos))
    rows = []
    for i in range(a.scenes):
        task = md.random_task(rnd)
        task.azimuth_step_deg = 45.0
        row = []
        for _, f in algos:
            r, dt = timed(lambda: f(task))
            row += [score(task, r), len(r), dt]
        rows.append(row)
        print(f"{i + 1:<3} " + " | ".join(f"{row[3 * k]:>17.1%} {row[3 * k + 1]:>5} {row[3 * k + 2]:>5.1f}"
                                          for k in range(len(algos))), flush=True)
    m = np.array(rows).mean(axis=0)
    print("ср. " + " | ".join(f"{m[3 * k]:>17.1%} {m[3 * k + 1]:>5.1f} {m[3 * k + 2]:>5.1f}" for k in range(len(algos))))


if __name__ == "__main__":
    main()
