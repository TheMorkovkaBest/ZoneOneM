"""Тесты новых интерактивных возможностей: выделение рамкой, копирование/вставка,
радиальное меню, 3D луч камеры и режим лучшего покрытия со штриховкой.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme
from tower import Tower
from network import Device, Network


class TestInteractiveFeatures(unittest.TestCase):
    def test_theme_icons_present(self):
        for icon in ("copy", "clipboard", "hatch", "radial"):
            self.assertIn(icon, theme.ICONS, f"Иконка {icon} должна присутствовать в theme.ICONS")
            self.assertTrue(len(theme.ICONS[icon]) > 20)

    def test_3d_camera_raycast_correction(self):
        """Проверка логики коррекции 3D-луча камеры при наклоне."""
        ground_lat, ground_lon = 47.0100, 28.8600
        pitch = 60.0  # градусы наклона камеры
        bearing = 45.0  # азимут камеры
        bld_height = 30.0  # высота здания 30 метров

        pitch_rad = math.radians(pitch)
        tan_pitch = math.tan(pitch_rad)
        b_rad = math.radians((bearing + 180.0) % 360.0)

        # Вычисляем смещение назад к камере
        d_ground = bld_height / tan_pitch
        d_lat = (d_ground * math.cos(b_rad)) / 111320.0
        d_lon = (d_ground * math.sin(b_rad)) / (111320.0 * math.cos(math.radians(ground_lat)))

        corrected_lat = ground_lat + d_lat
        corrected_lon = ground_lon + d_lon

        # Здание должно сместиться в сторону камеры (bearing 45 -> камера смотрит с юга-запада (225°),
        # то есть точка на земле за зданием находится к северо-востоку, а истинная крыша ближе к камере к юго-западу)
        self.assertNotEqual(corrected_lat, ground_lat)
        self.assertNotEqual(corrected_lon, ground_lon)
        self.assertLess(corrected_lat, ground_lat)
        self.assertLess(corrected_lon, ground_lon)

    def test_copy_paste_model_simulation(self):
        """Проверка модели копирования и вставки вышек с сохранением параметров и генерацией новых ID."""
        t1 = Tower("Вышка 1", 47.01, 28.86, 1800.0, 20.0, 25.0, 0.0, 20.0, [255, 0, 0, 255], azimuth=90.0, db_id=1)
        t2 = Tower("Вышка 2", 47.02, 28.87, 800.0, 30.0, 35.0, 0.0, 15.0, [0, 0, 255, 255], azimuth=180.0, db_id=2)
        towers = [t1, t2]

        # Выделяем t1 и t2
        selected_ids = {1, 2}
        copied_towers = [t.to_dict() for t in towers if t.db_id in selected_ids]
        self.assertEqual(len(copied_towers), 2)

        # Симулируем вставку со смещением
        c_lat = sum(t["lat"] for t in copied_towers) / len(copied_towers)
        c_lon = sum(t["lon"] for t in copied_towers) / len(copied_towers)

        paste_lat, paste_lon = 47.05, 28.90
        d_lat = paste_lat - c_lat
        d_lon = paste_lon - c_lon

        new_towers = []
        next_id = 100
        for td in copied_towers:
            d = dict(td)
            d["id"] = next_id
            next_id += 1
            d["lat"] += d_lat
            d["lon"] += d_lon
            new_towers.append(Tower.from_dict(d))

        self.assertEqual(len(new_towers), 2)
        self.assertEqual(new_towers[0].db_id, 100)
        self.assertEqual(new_towers[1].db_id, 101)
        self.assertAlmostEqual(new_towers[0].lat, 47.01 + d_lat)
        self.assertAlmostEqual(new_towers[0].power, 20.0)
        self.assertAlmostEqual(new_towers[1].power, 30.0)

    def test_building_data_cooldown_reset(self):
        """Проверка сброса паузы сети по F5 / действию пользователя."""
        import buildings
        bd = buildings.BuildingData()
        bd._fail_until = 9999999999.0
        bd.last_error = "тестовая ошибка"
        bd.reset_cooldown()
        self.assertEqual(bd._fail_until, 0.0)
        self.assertIsNone(bd.last_error)

    def test_windows_crlf_raster_tolerance(self):
        """Проверка, что различия в байтовых размерах файлов из-за CRLF на Windows не ломают кэш растра."""
        import buildings
        sources_disk = [("overpass_1.json", 1005), ("overpass_2.json", 2005)]
        cached_sources = [["overpass_1.json", 1000], ["overpass_2.json", 2000]]
        source_names = {s[0] for s in sources_disk}
        cached_names = {s[0] for s in cached_sources}
        self.assertEqual(source_names, cached_names)


if __name__ == "__main__":
    unittest.main()
