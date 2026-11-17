"""Тесты Алгоритма 3 (генетическая расстановка): операторы, ограничения, формат результата."""

import os
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
try:
    import _bootstrap
except ImportError:
    pass

import unittest
from unittest import mock

import numpy as np

import auto_placement
import coverage
import ga_placement as ga
from auto_placement import PlacementTask
from buildings import BuildingData
from test_ai_placement import LAT0, LON0, rect, small_city

FAST = dict(population=16, generations=15, patience=6, seed=5, seed_algorithm_1=False,
            use_surrogate=False, time_limit_s=60.0)


class TestGaPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bd = BuildingData()
        cls.bd.add_buildings(small_city())

    def task(self, **kw):
        args = dict(area=rect(-300, -300, 300, 300), forbidden=[rect(-60, -60, 60, 60)],
                    tower_type={"freq": 1800.0, "power": 10.0, "height": 5.0, "tilt": 6.0,
                                "wall_loss": 20.0, "azimuth": 0.0, "beamwidth": 360.0},
                    max_towers=4, building_data=self.bd)
        args.update(kw)
        return PlacementTask(**args)

    def placer(self, task, **kw):
        p = ga.GeneticPlacer(task, ga.GAParams(**{**FAST, **kw}))
        p.np_rnd = np.random.default_rng(0)
        p.ev = ga.CoverageEvaluator(task)
        p.pool = ga.CandidatePool(task)
        p.weight = p._prior(task)
        p.fit = ga.Fitness(task, p.pool, p.ev, p.az)
        return p

    def test_pool_points_are_allowed(self):
        t = self.task()
        pool = ga.CandidatePool(t)
        self.assertGreater(len(pool), 20)
        self.assertTrue(any(b is not None for b in pool.bids))       # есть крыши
        for la, lo in zip(pool.lats, pool.lons):
            self.assertTrue(t.is_allowed(la, lo))

    def test_fitness_matches_coverage_stats(self):
        t = self.task()
        p = self.placer(t)
        genes = [p.random_gene() for _ in range(3)]
        towers = [ga.make_tower(t, p.pool.lats[i], p.pool.lons[i], p.az[a]) for i, a in genes]
        st = coverage.coverage_stats(towers, t.area, t.forbidden, step_m=t.grid_step_m, max_points=25000)
        self.assertAlmostEqual(p.fit.coverage(genes), st["good_or_better"], places=6)
        f, cov, margin, cost, overlap = p.fit.details(genes)
        self.assertTrue(0.0 <= margin <= 1.0 and 0.0 <= overlap <= 1.0)
        self.assertAlmostEqual(f, p.fit(genes))

    def test_useless_tower_lowers_fitness(self):
        """Вышка, не добавляющая покрытия, только стоит денег и даёт перекрытие."""
        p = self.placer(self.task())
        g = p.random_gene()
        self.assertLess(p.fit([g, g]), p.fit([g]))

    def test_target_bonus(self):
        p = self.placer(self.task())
        g = [p.random_gene() for _ in range(2)]
        cov = p.fit.coverage(g)
        p.fit.target = cov - 1e-9
        p.fit._fit.clear()
        reached = p.fit(g)
        p.fit.target = cov + 1e-6
        p.fit._fit.clear()
        self.assertAlmostEqual(reached - p.fit(g), ga.TARGET_BONUS, places=4)

    def test_repair_limits(self):
        t = self.task(max_towers=2, tower_type={"freq": 1800.0, "power": 20.0, "height": 30.0, "tilt": 6.0,
                                                "wall_loss": 20.0, "azimuth": 0.0, "beamwidth": 65.0},
                      max_sectors_per_site=2)
        p = self.placer(t)
        genes = [(0, 0), (0, 0), (0, 1), (0, 2), (5, 0)]
        out = p.repair(genes)
        self.assertLessEqual(len(out), 2)
        self.assertEqual(len(out), len(set(out)))
        self.assertLessEqual(sum(1 for g in out if g[0] == 0), 2)

    def test_omni_antenna_one_per_site(self):
        p = self.placer(self.task())
        self.assertEqual(p.az, [0.0])
        self.assertEqual(p.repair([(3, 0), (3, 0)]), [(3, 0)])

    def test_crossover_takes_genes_from_parents(self):
        p = self.placer(self.task(max_towers=8))
        a = p.repair([p.random_gene() for _ in range(4)])
        b = p.repair([p.random_gene() for _ in range(4)])
        for _ in range(10):
            c1, c2 = p.crossover(a, b)
            self.assertTrue(set(c1) <= set(a) | set(b))
            self.assertTrue(set(c2) <= set(a) | set(b))
            self.assertTrue(c1 and c2)

    def test_mutate_add_targets_uncovered_point(self):
        p = self.placer(self.task(max_towers=5))
        g = [p.random_gene()]
        g2 = p.mutate_add(g)
        self.assertEqual(len(g2), 2)
        full = [p.random_gene() for _ in range(5)]
        self.assertEqual(p.mutate_add(full), full)                    # max_towers — не добавляем

    def test_ga_place_respects_constraints_and_format(self):
        t = self.task()
        res = ga.ga_place(t, ga.GAParams(**FAST))
        self.assertGreater(len(res), 0)
        self.assertLessEqual(len(res), t.max_towers)
        keys = {"lat", "lon", "height", "azimuth", "beamwidth", "building_id", "site_name",
                "gain_tiles", "quality_level"}
        for r in res:
            self.assertTrue(keys <= set(r))
            self.assertTrue(t.is_allowed(r["lat"], r["lon"]))
            self.assertGreaterEqual(r["gain_tiles"], t.min_gain_tiles)
        gains = [r["gain_tiles"] for r in res]
        self.assertEqual(gains, sorted(gains, reverse=True))         # в порядке вклада

    def test_ga_not_worse_than_its_seed(self):
        """С затравкой Алгоритма 1 элитизм не даёт результату стать хуже по целевой функции."""
        import algorithm_1
        t = self.task(max_towers=3, target_coverage_ratio=1.0)
        seed = algorithm_1.optimize_coverage(t)
        p = ga.GeneticPlacer(t, ga.GAParams(**{**FAST, "seed_algorithm_1": True}))
        res = p.run()
        cov = lambda pts: coverage.coverage_stats(
            [ga.make_tower(t, q["lat"], q["lon"], q["azimuth"]) for q in pts],
            t.area, t.forbidden, step_m=t.grid_step_m, max_points=25000)["good_or_better"]
        self.assertGreaterEqual(cov(res) + 0.02, cov(seed))
        self.assertTrue(p.history and all(b >= a for a, b in zip(p.history, p.history[1:])))

    def test_sector_azimuths(self):
        t = self.task(tower_type={"freq": 1800.0, "power": 20.0, "height": 30.0, "tilt": 6.0,
                                  "wall_loss": 20.0, "azimuth": 0.0, "beamwidth": 65.0}, max_towers=3)
        res = ga.ga_place(t, ga.GAParams(**FAST))
        self.assertTrue(res)
        self.assertTrue(all(r["azimuth"] in ga.azimuths_for(t) for r in res))

    def test_cancel_and_progress(self):
        calls = []
        t = self.task(progress_callback=lambda pct, msg, eta: calls.append(pct))
        ga.ga_place(t, ga.GAParams(**FAST))
        self.assertTrue(calls and all(0.0 <= c <= 99.0 for c in calls))
        t = self.task(is_cancelled=lambda: True)
        self.assertEqual(ga.ga_place(t, ga.GAParams(**FAST)), [])

    def test_forbidden_everywhere_returns_empty(self):
        t = self.task(forbidden=[rect(-400, -400, 400, 400)])
        self.assertEqual(ga.ga_place(t, ga.GAParams(**FAST)), [])

    def test_registry(self):
        name = "Алгоритм 3 (генетический)"
        self.assertIn(name, auto_placement.ALGORITHMS)
        with mock.patch.object(ga, "ga_place", return_value=[{"lat": 1, "lon": 2}]) as f:
            self.assertEqual(auto_placement.ALGORITHMS[name](self.task()), [{"lat": 1, "lon": 2}])
            f.assert_called_once()


if __name__ == "__main__":
    unittest.main()
