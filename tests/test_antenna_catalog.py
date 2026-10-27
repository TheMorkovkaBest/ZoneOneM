"""Тесты каталога антенн и опорных конструкций."""
import os
import sqlite3
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import antenna_catalog
import antenna_images
import structures
import theme


class TestAntennaCatalog(unittest.TestCase):
    def test_builtin_antennas_count_and_fields(self):
        self.assertGreaterEqual(len(antenna_catalog.BUILTIN_ANTENNAS), 35)
        for a in antenna_catalog.BUILTIN_ANTENNAS:
            self.assertTrue(a.id)
            self.assertTrue(a.manufacturer)
            self.assertTrue(a.model)
            self.assertTrue(a.category)
            self.assertGreater(a.freq_mhz, 0.0)
            self.assertGreater(a.gain_dbi, 0.0)
            self.assertGreater(a.tx_power_w, 0.0)
            self.assertGreater(a.beamwidth_h, 0.0)
            self.assertGreater(a.beamwidth_v, 0.0)
            self.assertGreaterEqual(a.wall_loss_db, 0.0)

    def test_init_and_load_antennas_db(self):
        conn = sqlite3.connect(":memory:")
        antennas = antenna_catalog.load_all_antennas(conn)
        self.assertGreaterEqual(len(antennas), 35)
        mfrs = {a.manufacturer for a in antennas}
        self.assertIn("Huawei", mfrs)
        self.assertIn("Ericsson", mfrs)
        self.assertIn("Kathrein", mfrs)
        self.assertIn("CommScope", mfrs)
        self.assertIn("Nokia", mfrs)
        self.assertIn("MikroTik", mfrs)
        self.assertIn("Ubiquiti", mfrs)

    def test_antenna_images_svg_and_pixmaps_for_all_models(self):
        """Проверяет, что для каждой модели в каталоге создаётся корректный SVG и QPixmap/QIcon."""
        conn = sqlite3.connect(":memory:")
        antennas = antenna_catalog.load_all_antennas(conn)
        self.assertGreaterEqual(len(antennas), 35)

        for a in antennas:
            svg = antenna_images.generate_antenna_svg(a, width=320, height=180, dark_theme=True)
            self.assertTrue(svg.startswith("<svg"))
            self.assertTrue(svg.endswith("</svg>"))
            # Валидный XML без синтаксических ошибок
            root = ET.fromstring(svg)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

            # Проверка генерации QPixmap и QIcon
            pix = antenna_images.get_antenna_pixmap(a, width=320, height=180, dark_theme=True)
            self.assertIsNotNone(pix)
            self.assertFalse(pix.isNull())
            self.assertEqual(pix.width(), 320)
            self.assertEqual(pix.height(), 180)

            icon = antenna_images.get_antenna_icon(a, size=32)
            self.assertIsNotNone(icon)
            self.assertFalse(icon.isNull())

    def test_structure_images_svg_and_pixmaps(self):
        """Проверяет генерацию схем для всех несущих конструкций вышек."""
        for spec in structures.BUILTIN_STRUCTURE_SPECS:
            svg = antenna_images.generate_structure_svg(spec, width=320, height=180, dark_theme=True)
            self.assertTrue(svg.startswith("<svg"))
            self.assertTrue(svg.endswith("</svg>"))
            root = ET.fromstring(svg)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

            pix = antenna_images.get_structure_pixmap(spec, width=320, height=180, dark_theme=True)
            self.assertIsNotNone(pix)
            self.assertFalse(pix.isNull())

    def test_antenna_catalog_dialog_preview_and_icons(self):
        """Проверяет, что диалог каталога заполняет иконки строк и отображает картинку выбранной модели."""
        conn = sqlite3.connect(":memory:")
        dlg = antenna_catalog.AntennaCatalogDialog(conn)
        self.assertTrue(hasattr(dlg, "lbl_image"))
        self.assertGreaterEqual(dlg.table.rowCount(), 35)

        # Первая колонка таблицы должна содержать иконку оборудования
        first_item = dlg.table.item(0, 0)
        self.assertIsNotNone(first_item)
        self.assertFalse(first_item.icon().isNull())

        # Проверяем отрисовку изображения при выборе
        dlg.table.selectRow(0)
        self.assertIsNotNone(dlg.selected_antenna)
        self.assertIsNotNone(dlg.lbl_image.pixmap())
        self.assertFalse(dlg.lbl_image.pixmap().isNull())

    def test_theme_structure_icon_present(self):
        self.assertIn("structure", theme.ICONS)
        svg_content = theme.ICONS["structure"]
        self.assertIn("line", svg_content)


if __name__ == "__main__":
    unittest.main()
