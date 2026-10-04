import json
import os
import random
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "Node.js не найден — тест JS пропущен")
class TestJs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rnd = random.Random(5)
        cls.cases = [[rnd.uniform(-60, 60), rnd.uniform(-179, 179), rnd.uniform(-60, 60), rnd.uniform(-179, 179)]
                     for _ in range(200)]
        p = subprocess.run(["node", os.path.join(HERE, "js_harness.js")], input=json.dumps({"geo": cls.cases}),
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError(p.stderr)
        cls.out = json.loads(p.stdout)

    def test_geo_matches_python(self):
        for c, r in zip(self.cases, self.out["geo"]):
            self.assertAlmostEqual(r["hav"], geo.haversine_m(*c), delta=0.01)
            d = abs(r["brg"] - geo.bearing_deg(*c))
            self.assertLess(min(d, 360 - d), 1e-6)

    def test_formatting(self):
        self.assertEqual(self.out["checks"]["fmt"], [geo.format_distance(250), geo.format_distance(1500),
                                                     geo.format_bearing(45).replace("° (", "° ").rstrip(")")])

    def test_map_scenario(self):
        c = self.out["checks"]
        self.assertTrue(c["ready"])
        self.assertTrue(c["layers"])
        self.assertEqual(c["coverageFeatures"], 8)
        self.assertTrue(c["sectorRingStartsAtCenter"])
        self.assertEqual(c["sectorRingLen"], 35)
        self.assertTrue(c["sectorFirstWest"])
        self.assertEqual(c["select"], ["select:1"])        # клик мимо вышки в режиме навигации молчит
        self.assertEqual(c["measurePts"], 2)
        self.assertEqual(c["measureMarkers"], 2)          # подпись сегмента + «Σ итого»
        self.assertEqual(c["measureUndo"], 1)
        self.assertEqual(c["zonePoly"], "algo:4")
        self.assertTrue(c["zoneRect"].startswith("forbidden:4:[11,12]"))
        self.assertTrue(c["dblZoomDisabled"])
        self.assertTrue(c["dblZoomRestored"])
        self.assertEqual(c["misc"], ["place", "probe", "hover_load"])
        self.assertEqual(c["esc"], ["mode:navigate"])
        self.assertTrue(c["styleSwitch"])

    def test_azimuth_handle(self):
        c = self.out["checks"]
        self.assertTrue(c["noHandleForOmni"])               # у круговой вышки азимут ни на что не влияет
        self.assertTrue(c["handleExists"])
        self.assertTrue(c["previewEast"])                   # сектор повернулся к востоку ещё до пересчёта
        self.assertEqual(c["azMsg"], [[2, 90]])
        self.assertTrue(c["previewCleared"])
        self.assertTrue(c["handleHiddenInMeasure"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
