"""Тесты модуля учёта неровностей рельефа местности (Terrain / DEM)."""

import unittest
from pathlib import Path
import numpy as np

import calculations as C
from buildings import BuildingData
from terrain import TerrainData
from tower import Tower


class TestTerrain(unittest.TestCase):
    def setUp(self):
        self.td = TerrainData(cache_dir="buildings_cache")

    def test_terrain_cache_and_point_lookup(self):
        """Проверка кэширования высот рельефа в памяти и на диске."""
        lat, lon = 55.7512, 37.6184
        self.td._point_cache[(round(lat, 4), round(lon, 4))] = 145.0
        self.assertEqual(self.td.get_elevation(lat, lon), 145.0)

        # Проверка сохранения и загрузки
        self.td.save_cache()
        new_td = TerrainData(cache_dir="buildings_cache")
        self.assertEqual(new_td.get_elevation(lat, lon), 145.0)

    def test_terrain_heights_along_ray(self):
        """Проверка вычисления профиля рельефа вдоль луча."""
        lat, lon = 55.7500, 37.6100
        # Заполняем искусственный холм по направлению на восток
        for i in range(10):
            p_lat = lat
            p_lon = lon + (i * 100.0) / (C.M_PER_DEG_LAT * 0.56)
            self.td._point_cache[(round(p_lat, 4), round(p_lon, 4))] = 130.0 + i * 5.0

        dists = np.linspace(0, 500, 50)
        ux, uy = 1.0, 0.0  # восток
        rel = self.td.heights_along(lat, lon, ux, uy, dists, base_elevation=130.0)

        self.assertEqual(len(rel), len(dists))
        # С удалением на восток относительная высота холма растёт
        self.assertGreater(rel[-1], rel[0])
        self.assertGreater(rel[-1], 20.0)

    def test_terrain_integration_with_building_data(self):
        """Проверка интеграции рельефа с профилем затухания в зданиях."""
        bd = BuildingData()
        bd.terrain_data = self.td
        lat, lon = 55.7500, 37.6100
        # Моделируем высокий холм впереди (+30 м)
        for i in range(10):
            p_lat = lat
            p_lon = lon + (i * 100.0) / (C.M_PER_DEG_LAT * 0.56)
            self.td._point_cache[(round(p_lat, 4), round(p_lon, 4))] = 120.0 + i * 6.0

        dists = np.array([50.0, 150.0, 300.0])
        h_with_terrain = bd.heights_along(lat, lon, 1.0, 0.0, dists)
        self.assertGreater(h_with_terrain[-1], 15.0)

        # При отключенном рельефе высота должна быть 0 (зданий нет)
        bd.terrain_data = None
        h_without_terrain = bd.heights_along(lat, lon, 1.0, 0.0, dists)
        self.assertTrue(np.all(h_without_terrain == 0.0))

    def test_link_analysis_includes_terrain_profile(self):
        """Проверка, что зонд сигнала и профиль трассы содержат неровности рельефа."""
        bd = BuildingData()
        bd.terrain_data = self.td
        tw = Tower("T1", 55.7500, 37.6100, 1800, 20.0, 25.0, 0.0, 15.0, [255, 0, 0, 255])

        # Анализ радиолинии к точке в 200 метрах
        target_lat = 55.7500
        target_lon = 37.6100 + 200.0 / (C.M_PER_DEG_LAT * 0.56)
        res = C.link_analysis(tw, target_lat, target_lon, building_data=bd)

        self.assertIn("profile", res)
        self.assertIn("terrain_h", res["profile"])
        self.assertEqual(len(res["profile"]["terrain_h"]), len(res["profile"]["dist"]))


if __name__ == "__main__":
    unittest.main()
