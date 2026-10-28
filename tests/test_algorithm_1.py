"""Тесты Алгоритма 1: Оптимизация покрытия (Discrete Azimuth Greedy Set-Cover)."""

try:
    import _bootstrap
except ImportError:
    pass

import unittest

import algorithm_1
import auto_placement
from auto_placement import PlacementTask


class TestAlgorithm1(unittest.TestCase):
    def setUp(self):
        # Полигон ~500x500 м
        self.area_poly = [
            (55.7500, 37.6100),
            (55.7550, 37.6100),
            (55.7550, 37.6180),
            (55.7500, 37.6180),
        ]
        self.tower_type = {
            "freq": 1800.0,
            "power": 20.0,
            "height": 30.0,
            "tilt": 4.0,
            "wall_loss": 20.0,
            "beamwidth": 65.0,
        }

    def test_find_candidate_sites_without_buildings(self):
        """Без зданий кандидаты создаются как опоры на рельефе."""
        sites = algorithm_1.find_candidate_sites(
            area_poly=self.area_poly,
            forbidden_polys=[],
            building_data=None,
            default_height=30.0,
            cluster_size_m=200.0,
        )
        self.assertGreater(len(sites), 0)
        for s in sites:
            self.assertEqual(s.height, 30.0)
            self.assertIsNone(s.building_id)

    def test_find_candidate_sites_with_buildings(self):
        """При наличии зданий выбирается самое высокое в кластере."""
        class MockBuildingData:
            buildings = [
                {
                    "id": "bld_1",
                    "height": 45.0,
                    "poly": [
                        (55.7510, 37.6110),
                        (55.7515, 37.6110),
                        (55.7515, 37.6115),
                        (55.7510, 37.6115),
                    ],
                },
                {
                    "id": "bld_2",
                    "height": 15.0,
                    "poly": [
                        (55.7510, 37.6120),
                        (55.7512, 37.6120),
                        (55.7512, 37.6122),
                        (55.7510, 37.6122),
                    ],
                },
            ]

        sites = algorithm_1.find_candidate_sites(
            area_poly=self.area_poly,
            forbidden_polys=[],
            building_data=MockBuildingData(),
            default_height=30.0,
            cluster_size_m=200.0,
        )
        self.assertGreater(len(sites), 0)
        bld_ids = {s.building_id for s in sites if s.building_id}
        self.assertIn("bld_1", bld_ids)
        # Высота должна включать высоту крыши + мачту
        bld_site = next(s for s in sites if s.building_id == "bld_1")
        self.assertEqual(bld_site.height, 50.0)  # 45 + 5

    def test_optimize_coverage_runs_and_returns_valid_sectors(self):
        """Алгоритм отбирает секторы с оптимальными азимутами и координатами."""
        task = PlacementTask(
            area=self.area_poly,
            forbidden=[],
            tower_type=self.tower_type,
            max_towers=4,
            building_data=None,
        )
        res = algorithm_1.optimize_coverage(task, grid_step_m=30.0, azimuth_step_deg=45.0)
        self.assertGreater(len(res), 0)
        self.assertLessEqual(len(res), 4)

        for sector in res:
            self.assertIn("lat", sector)
            self.assertIn("lon", sector)
            self.assertIn("azimuth", sector)
            self.assertIn("height", sector)
            self.assertIn("beamwidth", sector)
            self.assertEqual(sector["beamwidth"], 65.0)
            self.assertGreaterEqual(sector["azimuth"], 0.0)
            self.assertLess(sector["azimuth"], 360.0)

    def test_forbidden_zones_respected(self):
        """Вышки не должны размещаться внутри запретных зон."""
        forbidden_zone = [
            (55.7505, 37.6105),
            (55.7525, 37.6105),
            (55.7525, 37.6135),
            (55.7505, 37.6135),
        ]
        task = PlacementTask(
            area=self.area_poly,
            forbidden=[forbidden_zone],
            tower_type=self.tower_type,
            max_towers=5,
            building_data=None,
        )
        res = algorithm_1.optimize_coverage(task, grid_step_m=30.0)
        for s in res:
            self.assertTrue(task.is_allowed(s["lat"], s["lon"]))

    def test_integration_with_auto_placement(self):
        """Интеграция через реестр auto_placement.ALGORITHMS."""
        self.assertIn("Алгоритм 1 (жадный с перебором азимутов)", auto_placement.ALGORITHMS)
        func = auto_placement.ALGORITHMS["Алгоритм 1 (жадный с перебором азимутов)"]
        task = PlacementTask(
            area=self.area_poly,
            forbidden=[],
            tower_type=self.tower_type,
            max_towers=3,
            building_data=None,
        )
        points = func(task)
        self.assertIsInstance(points, list)
        self.assertGreater(len(points), 0)

    def test_c_acceleration_functions(self):
        """Тест Си-ядра оптимизации покрытия и жадного отбора."""
        import c_coverage
        import numpy as np

        if not c_coverage.is_c_available():
            self.skipTest("C-библиотека libc_coverage недоступна на данной платформе")

        # Проверка c_compute_sector_mask
        lats = np.array([55.750, 55.751, 55.752], dtype=np.float64)
        lons = np.array([37.610, 37.611, 37.612], dtype=np.float64)
        reaches = np.full(72, 1000.0, dtype=np.float64)

        mask, covered = c_coverage.c_compute_sector_mask(lats, lons, 55.750, 37.610, reaches)
        self.assertEqual(covered, 3)
        self.assertEqual(len(mask), 1)
        self.assertEqual(int(mask[0]) & 0b111, 0b111)

        # Проверка c_greedy_set_cover
        cand_masks = np.array([
            [0b0011],  # точки 0, 1
            [0b1100],  # точки 2, 3
            [0b0001],  # дубль точки 0
        ], dtype=np.uint64)
        site_ids = [0, 1, 0]

        selected = c_coverage.c_greedy_set_cover(
            cand_masks=cand_masks,
            site_ids=site_ids,
            num_tiles=4,
            max_towers=2,
            min_gain_tiles=1,
            target_ratio=1.0,
        )
        self.assertEqual(set(selected), {0, 1})

    def test_quality_level_average_coverage(self):
        """Проверка расчёта покрытия для средней зоны (good, >= -85 дБм)."""
        task = PlacementTask(
            area=self.area_poly,
            forbidden=[],
            tower_type=self.tower_type,
            max_towers=5,
            target_coverage_ratio=0.95,
            min_quality_level="good",
        )
        res = algorithm_1.optimize_coverage(task, grid_step_m=30.0)
        self.assertGreater(len(res), 0)
        for s in res:
            self.assertEqual(s.get("quality_level"), "good")
            self.assertGreater(s.get("gain_tiles", 0), 0)

    def test_progress_callback_and_cancellation(self):
        """Проверка вызова progress_callback с оценкой ETA и досрочной отмены."""
        progress_events = []

        def on_prog(pct, msg, eta_s):
            progress_events.append((pct, msg, eta_s))

        task = PlacementTask(
            area=self.area_poly,
            forbidden=[],
            tower_type=self.tower_type,
            max_towers=3,
            progress_callback=on_prog,
        )
        res = algorithm_1.optimize_coverage(task, grid_step_m=30.0)
        self.assertGreater(len(res), 0)
        self.assertGreater(len(progress_events), 0)
        # Проверяем, что прогресс дошёл до 100%
        self.assertEqual(progress_events[-1][0], 100.0)

        # Тест отмены
        task_cancelled = PlacementTask(
            area=self.area_poly,
            forbidden=[],
            tower_type=self.tower_type,
            max_towers=5,
            is_cancelled=lambda: True,
        )
        res_cancelled = algorithm_1.optimize_coverage(task_cancelled, grid_step_m=30.0)
        self.assertEqual(res_cancelled, [])


if __name__ == "__main__":
    unittest.main()
