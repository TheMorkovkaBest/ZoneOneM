"""Тесты каталога антенн и опорных конструкций."""
import sqlite3
import unittest
from pathlib import Path

import antenna_catalog
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

    def test_theme_structure_icon_present(self):
        self.assertIn("structure", theme.ICONS)
        svg_content = theme.ICONS["structure"]
        self.assertIn("line", svg_content)


if __name__ == "__main__":
    unittest.main()
