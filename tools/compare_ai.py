"""Сравнение Алгоритма 2 (ИИ) с Алгоритмом 1 на новых случайных участках — таблица для отчёта.

    python tools/compare_ai.py --scenes 10

Участки берутся с другим зерном, чем при обучении, поэтому сеть их «не видела».
Покрытие считается одинаково для обоих: coverage.coverage_stats.
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
import coverage                                      # noqa: E402
import make_dataset as md                            # noqa: E402

KEY = {"weak": "covered", "good": "good_or_better"}


def score(task, points):
    towers = [ai.make_tower(task, p["lat"], p["lon"], p.get("azimuth", 0.0)) for p in points]
    st = coverage.coverage_stats(towers, task.area, task.forbidden, step_m=task.grid_step_m, max_points=25000)
    return st[KEY.get(task.min_quality_level, "good_or_better")] if st else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", type=int, default=10)
    ap.add_argument("--seed", type=int, default=777)
    ap.add_argument("--model", default=ai.MODEL_PATH)
    a = ap.parse_args()
    if not os.path.exists(a.model):
        sys.exit(f"Нет модели {a.model}. Сначала обучите: python tools/train_ai.py")
    model = ai.load_model(a.model)
    rnd = random.Random(a.seed)
    rows = []
    print(f"{'№':>3} {'Алг.1: покр.':>13} {'вышек':>6} {'время,с':>8} | {'ИИ: покр.':>10} {'вышек':>6} {'время,с':>8}")
    for i in range(a.scenes):
        task = md.random_task(rnd)
        task.azimuth_step_deg = 45.0
        t0 = time.perf_counter(); r1 = algorithm_1.optimize_coverage(task); t1 = time.perf_counter() - t0
        t0 = time.perf_counter(); r2 = ai.ai_place(task, model); t2 = time.perf_counter() - t0
        c1, c2 = score(task, r1), score(task, r2)
        rows.append((c1, len(r1), t1, c2, len(r2), t2))
        print(f"{i + 1:>3} {c1:>12.1%} {len(r1):>6} {t1:>8.1f} | {c2:>9.1%} {len(r2):>6} {t2:>8.1f}", flush=True)
    m = np.array(rows).mean(axis=0)
    print(f"{'ср.':>3} {m[0]:>12.1%} {m[1]:>6.1f} {m[2]:>8.1f} | {m[3]:>9.1%} {m[4]:>6.1f} {m[5]:>8.1f}")


if __name__ == "__main__":
    main()
