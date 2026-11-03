"""Тесты Алгоритма 2 (ИИ-расстановка): признаки, сеть, расстановка, запасной путь."""

try:
    import _bootstrap
except ImportError:
    pass

import os
import random
import tempfile
import unittest
from unittest import mock

import numpy as np

import ai_model
import ai_placement as ai
import auto_placement
import geo
from auto_placement import PlacementTask
from buildings import BuildingData

LAT0, LON0 = 47.0101, 28.8638
M_LAT = 111320.0
M_LON = M_LAT * np.cos(np.radians(LAT0))


def rect(x0, y0, x1, y1):
    return [(LAT0 + y0 / M_LAT, LON0 + x0 / M_LON), (LAT0 + y0 / M_LAT, LON0 + x1 / M_LON),
            (LAT0 + y1 / M_LAT, LON0 + x1 / M_LON), (LAT0 + y1 / M_LAT, LON0 + x0 / M_LON)]


def small_city():
    rnd = random.Random(3)
    out = []
    for i in range(-6, 7):
        for j in range(-6, 7):
            if rnd.random() < 0.4:
                continue
            x, y = i * 60.0, j * 60.0
            out.append({"id": f"b{i}_{j}", "height": float(rnd.choice([9, 15, 27])),
                        "poly": rect(x - 12, y - 12, x + 12, y + 12)})
    return out


class TestAiPlacement(unittest.TestCase):
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

    def test_features_shape_and_masks(self):
        t = self.task()
        X, grid, allowed = ai.build_features(t, [])
        self.assertEqual(X.shape, (ai.N_CHANNELS, ai.GRID_N, ai.GRID_N))
        r, c = grid.cell_of(LAT0, LON0)
        self.assertEqual(X[1, r, c], 1.0)            # центр — запретная зона
        self.assertFalse(allowed[r, c])
        self.assertGreater(X[3].sum(), 0)            # есть крыши-кандидаты
        self.assertEqual(X[4].max(), 0.0)            # вышек ещё нет — покрытия нет

    def test_network_shapes_and_save_load(self):
        net = ai_model.SurrogateNet(ai.N_CHANNELS, width=8)
        X, _, _ = ai.build_features(self.task(), [])
        p = net.predict(X)
        self.assertEqual(p.shape, (ai.GRID_N, ai.GRID_N))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.npz")
            net.save(path)
            np.testing.assert_allclose(ai_model.SurrogateNet.load(path).predict(X), p, rtol=1e-5)

    def test_ai_place_respects_constraints(self):
        t = self.task()
        net = ai_model.SurrogateNet(ai.N_CHANNELS, width=8)   # даже необученная сеть даёт корректный результат
        res = ai.ai_place(t, net)
        self.assertGreater(len(res), 0)
        self.assertLessEqual(len(res), t.max_towers)
        for p in res:
            self.assertTrue(t.is_allowed(p["lat"], p["lon"]))
            self.assertIn("azimuth", p)
            self.assertGreater(p["gain_tiles"], 0)

    def test_coverage_grows_and_matches_coverage_stats(self):
        import coverage
        t = self.task(max_towers=3, target_coverage_ratio=1.0)
        res = ai.ai_place(t, ai_model.SurrogateNet(ai.N_CHANNELS, width=8))
        towers = [ai.make_tower(t, p["lat"], p["lon"], p["azimuth"]) for p in res]
        stats = coverage.coverage_stats(towers, t.area, t.forbidden, step_m=t.grid_step_m, max_points=25000)
        ev = ai.CoverageEvaluator(t)
        for tw in towers:
            ev.add(ev.try_tower(tw)[1])
        self.assertAlmostEqual(ev.ratio(), stats["good_or_better"], places=6)

    def test_sector_antenna_gets_azimuth(self):
        t = self.task(tower_type={"freq": 1800.0, "power": 20.0, "height": 30.0, "tilt": 6.0,
                                  "wall_loss": 20.0, "azimuth": 0.0, "beamwidth": 65.0}, max_towers=2)
        res = ai.ai_place(t, ai_model.SurrogateNet(ai.N_CHANNELS, width=8))
        self.assertTrue(res)
        self.assertTrue(all(p["azimuth"] in ai.azimuths_for(t) for p in res))

    def test_cancel(self):
        t = self.task(is_cancelled=lambda: True)
        self.assertEqual(ai.ai_place(t, ai_model.SurrogateNet(ai.N_CHANNELS, width=8)), [])

    def test_registry_falls_back_to_algorithm_1_without_model(self):
        name = "Алгоритм 2 (ИИ: нейросеть + проверка)"
        self.assertIn(name, auto_placement.ALGORITHMS)
        with mock.patch.object(ai, "load_model", return_value=None), \
                mock.patch.object(auto_placement, "algorithm_1", return_value=[{"lat": 1, "lon": 2}]) as a1:
            self.assertEqual(auto_placement.ALGORITHMS[name](self.task()), [{"lat": 1, "lon": 2}])
            a1.assert_called_once()

    def test_polygon_helpers_consistent(self):
        t = self.task()
        g = ai.Grid(t.area)
        r, c = g.cell_of(LAT0 + 200 / M_LAT, LON0 + 200 / M_LON)
        self.assertTrue(geo.point_in_polygon(g.lats[r, c], g.lons[r, c], t.area))


if __name__ == "__main__":
    unittest.main()
