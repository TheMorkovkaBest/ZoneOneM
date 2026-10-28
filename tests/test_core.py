try:
    import _bootstrap
except ImportError:
    pass

import json
import math
import os
import random
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calculations as C            # noqa: E402
import coverage                     # noqa: E402
import exporters                    # noqa: E402
import geo                          # noqa: E402
from buildings import BuildingData, parse_height   # noqa: E402
from tower import Tower, TowerDB    # noqa: E402

LAT0, LON0 = 47.0101, 28.8638
M_LON = C.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def rect(cx, cy, w, h, ang=0.0):
    """Прямоугольник (центр и размеры в метрах от LAT0/LON0) -> полигон (lat, lon)."""
    a = math.radians(ang)
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = sx * w / 2, sy * h / 2
        pts.append((LAT0 + (cy + x * math.sin(a) + y * math.cos(a)) / C.M_PER_DEG_LAT,
                    LON0 + (cx + x * math.cos(a) - y * math.sin(a)) / M_LON))
    return pts


def make_city(n=80, seed=3, extent=500.0):
    rnd = random.Random(seed)
    items = []
    for k in range(n):
        items.append({"id": k, "height": float(rnd.choice([6, 12, 15, 20, 30])),
                      "poly": rect(rnd.uniform(-extent, extent), rnd.uniform(-extent, extent),
                                   rnd.uniform(10, 45), rnd.uniform(10, 45), rnd.uniform(0, 90))})
    bd = BuildingData()
    bd.add_buildings(items)
    return bd


def reference_reach(tw, bd):
    """Медленная скалярная эталонная реализация той же модели (цикл по лучам и шагам)."""
    half_m = min(C.calc_base_radius(tw.freq, tw.power, tw.height, -105, tw.beamwidth), C.WINDOW_HALF_M)
    n_steps = int(half_m // C.GRID_RES_M)
    occ, px, py = bd.occupancy_window(tw.lat, tw.lon, half_m, tw.height)
    allowed = C.eirp_dbm(tw.power, tw.beamwidth) - C.ZONE_THRESH
    out = np.zeros((C.NUM_RAYS, 4))
    for i, a in enumerate(C.ray_angles()):
        if not C.is_in_sector(a, tw.azimuth, tw.beamwidth):
            continue
        dx, dy = math.sin(math.radians(a)), math.cos(math.radians(a))
        prev, seen_free, walls, inside = False, False, 0, 0
        reach = [None] * 4
        wall_prev = 0.0
        for s in range(1, n_steps + 1):
            cx, cy = int(math.floor(px + dx * s)), int(math.floor(py + dy * s))
            hit = bool(occ[cy, cx])
            if not hit:
                seen_free = True
            hit = hit and seen_free
            walls += hit != prev
            inside += hit
            prev = hit
            cum = walls * tw.wall_loss + inside * C.GRID_RES_M * C.INDOOR_LOSS_DB_PER_M
            loss = C.city_path_loss_db(s * C.GRID_RES_M, tw.freq, tw.height) + cum
            for z in range(4):
                if reach[z] is None and loss > allowed[z]:
                    far = C.distance_for_loss_db(allowed[z] - wall_prev, tw.freq, tw.height)
                    reach[z] = min(max(far, (s - 1) * C.GRID_RES_M), s * C.GRID_RES_M)
            wall_prev = cum
        base = C.distance_for_loss_db(allowed, tw.freq, tw.height)
        for z in range(4):
            if reach[z] is None:
                rem = allowed[z] - (walls * tw.wall_loss + inside * C.GRID_RES_M * C.INDOOR_LOSS_DB_PER_M)
                reach[z] = max(C.distance_for_loss_db(rem, tw.freq, tw.height), n_steps * C.GRID_RES_M)
            out[i, z] = min(reach[z], base[z])
    return out


class TestRadioModel(unittest.TestCase):
    def test_closed_form_matches_old_bisection(self):
        def old(freq, power, h, th):
            budget = 10 * math.log10(power * 1000) + C.G_TX_BASE - th
            cap = 350.0 if h <= 6 else (1200.0 if freq >= 1800 else 3500.0)
            lo, hi = 1.0, cap
            for _ in range(12):
                mid = (lo + hi) / 2
                if C.city_path_loss_db(mid, freq, h) <= budget:
                    lo = mid
                else:
                    hi = mid
            return lo
        for f, p, h in [(1800, 10, 5), (800, 40, 35), (2600, 1, 3), (900, 20, 20)]:
            for _, th in C.THRESHOLDS:
                new = C.calc_base_radius(f, p, h, th)
                cap = C.max_range_cap_m(f, h)
                self.assertLess(abs(new - old(f, p, h, th)), cap / 4096 + 1e-6, (f, p, h, th))

    def test_zones_nested_and_sector(self):
        r = C.compute_ray_reach(LAT0, LON0, 1800, 20, 30, 20, None, azimuth=90, beamwidth=65, pattern=False)
        self.assertTrue(np.all(np.diff(r.reach, axis=1) >= 0))        # green<=exc<=good<=weak
        angles = r.angles[r.reach[:, 3] > 0]
        self.assertTrue(np.all(np.abs((angles - 90 + 540) % 360 - 180) <= 32.5))

    def test_antenna_pattern_back_lobe(self):
        r = C.compute_ray_reach(LAT0, LON0, 1800, 0.05, 30, 20, None, azimuth=90, beamwidth=65)
        self.assertTrue(np.all(np.diff(r.reach, axis=1) >= 0))
        self.assertLess(r.reach[45, 3], C.max_range_cap_m(1800, 30))
        front, side, back = r.reach[45, 3], r.reach[0, 3], r.reach[135, 3]   # 90°, 0°, 270°
        self.assertGreater(back, 0.0)                                  # задний лепесток: сигнал есть
        self.assertLess(back, front / 3)                               # но на 25 дБ слабее
        self.assertLessEqual(back, side + 1e-9)
        # граница 3 дБ: на краю луча (±32.5°) бюджет меньше ровно на 3 дБ
        self.assertAlmostEqual(C.pattern_attenuation_db(90 + 32.5, 90, 65), 3.0, places=6)
        self.assertEqual(C.pattern_attenuation_db(270, 90, 65), C.FRONT_TO_BACK_DB)
        self.assertEqual(C.pattern_attenuation_db(123, 0, 360), 0.0)  # круговая — без ослабления

    def test_knife_edge(self):
        self.assertAlmostEqual(float(C.knife_edge_db(0.0)), 6.0, delta=0.05)   # кромка на линии визирования
        self.assertEqual(float(C.knife_edge_db(-1.0)), 0.0)                    # зона Френеля свободна
        self.assertGreater(float(C.knife_edge_db(2.0)), 16.0)

    def test_quality_levels(self):
        self.assertEqual(C.quality_of(-50), "green")
        self.assertEqual(C.quality_of(-100), "weak")
        self.assertEqual(C.quality_of(-120), "none")


class TestBuildings(unittest.TestCase):
    def test_parse_height(self):
        self.assertEqual(parse_height({"height": "12.5 m"}), 12.5)
        self.assertEqual(parse_height({"height": "12,5"}), 12.5)
        self.assertEqual(parse_height({"building:levels": "5"}), 15.0)      # раньше было 5 м (баг)
        self.assertAlmostEqual(parse_height({"height": "30 ft"}), 9.144)
        self.assertEqual(parse_height({"building": "garage"}), 3.0)
        self.assertEqual(parse_height({"building": "yes"}), 15.0)
        self.assertEqual(parse_height({"height": "abc"}), 15.0)

    def test_rasterized_area_matches_polygon(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 20, "poly": rect(0, 0, 60, 30, ang=33)}])
        occ, px, py = bd.occupancy_window(LAT0, LON0, 80, 10)
        area = occ.sum() * C.GRID_RES_M ** 2
        self.assertLess(abs(area - 1800) / 1800, 0.05)
        self.assertTrue(occ[int(py), int(px)])                  # центр здания занят

    def test_height_filter_and_dedup(self):
        bd = BuildingData()
        item = {"id": "a", "height": 10, "poly": rect(0, 0, 40, 40)}
        self.assertEqual(bd.add_buildings([item]), 1)
        self.assertEqual(bd.add_buildings([item]), 0)           # дубль отброшен
        self.assertEqual(len(bd.buildings), 1)
        self.assertTrue(bd.occupancy_window(LAT0, LON0, 50, 10)[0].any())
        self.assertFalse(bd.occupancy_window(LAT0, LON0, 50, 11)[0].any())   # башня выше здания

    def test_no_data_returns_none(self):
        self.assertIsNone(BuildingData().occupancy_window(LAT0, LON0, 50, 5))

    def test_overpass_relation_and_tagless_ways(self):
        nodes = [{"type": "node", "id": i, "lat": LAT0 + dy * 1e-4, "lon": LON0 + dx * 1e-4}
                 for i, (dx, dy) in enumerate([(0, 0), (1, 0), (1, 1), (0, 1), (5, 5), (6, 5), (6, 6)], 1)]
        data = {"elements": nodes + [
            {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1], "tags": {"building": "yes", "building:levels": "4"}},
            {"type": "way", "id": 11, "nodes": [5, 6, 7, 5]},                      # без тегов
            {"type": "relation", "id": 20, "tags": {"building": "yes", "height": "8"},
             "members": [{"type": "way", "ref": 11, "role": "outer"}]},
        ]}
        b = BuildingData()._parse_overpass(data)
        self.assertEqual(sorted((x["id"], x["height"]) for x in b), [("r20_0", 8.0), ("w10", 12.0)])


class TestAttenuation(unittest.TestCase):
    def test_wall_blocks_but_does_not_cut_off(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 30, "poly": rect(0, 80, 60, 20)}])   # дом к северу от вышки
        free = C.compute_ray_reach(LAT0, LON0, 1800, 10, 5, 20, None)
        with_b = C.compute_ray_reach(LAT0, LON0, 1800, 10, 5, 20, bd)
        self.assertTrue(with_b.used_buildings)
        north = 0
        self.assertLess(with_b.reach[north, 3], free.reach[north, 3])      # тень за домом
        self.assertGreater(with_b.reach[north, 3], 70.0)                   # но сигнал прошёл сквозь стены
        south = 90
        self.assertAlmostEqual(with_b.reach[south, 3], free.reach[south, 3], places=3)

    def test_low_building_ignored_without_fresnel(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 4, "poly": rect(0, 80, 60, 20)}])
        a = C.compute_ray_reach(LAT0, LON0, 1800, 10, 5, 20, None, fresnel=False)
        b = C.compute_ray_reach(LAT0, LON0, 1800, 10, 5, 20, bd, fresnel=False)
        np.testing.assert_allclose(a.reach, b.reach)

    def test_low_building_in_fresnel_zone_shadows(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 4, "poly": rect(0, 80, 60, 20)}])     # ниже антенны 5 м
        free = C.compute_ray_reach(LAT0, LON0, 1800, 0.2, 5, 20, None)
        b = C.compute_ray_reach(LAT0, LON0, 1800, 0.2, 5, 20, bd)
        north, south = 0, 90
        self.assertLess(free.reach[north, 2], C.max_range_cap_m(1800, 5))   # зона «средняя» не упирается в потолок
        self.assertLess(b.reach[north, 2], free.reach[north, 2] - 5)   # тень дифракции за низким домом
        self.assertGreater(b.reach[north, 2], 70.0)                    # до дома (70–90 м) сигнал доходит
        self.assertAlmostEqual(b.reach[south, 2], free.reach[south, 2], places=3)
        tw = Tower("t", LAT0, LON0, 1800, 0.2, 5, 0, 20, [1, 2, 3, 255], building_data=bd)
        behind = C.link_analysis(tw, LAT0 + 130 / C.M_PER_DEG_LAT, LON0, bd)
        self.assertGreater(behind["diffraction_db"], 3.0)
        tw_off = Tower("t", LAT0, LON0, 1800, 0.2, 5, 0, 20, [1, 2, 3, 255], building_data=bd, fresnel=False)
        self.assertEqual(C.link_analysis(tw_off, LAT0 + 130 / C.M_PER_DEG_LAT, LON0, bd)["diffraction_db"], 0.0)

    def test_fresnel_clear_path_no_loss(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 3, "poly": rect(0, 60, 30, 10)}])     # низкий сарай, макросота 35 м
        a = C.compute_ray_reach(LAT0, LON0, 800, 40, 35, 15, None)
        b = C.compute_ray_reach(LAT0, LON0, 800, 40, 35, 15, bd)
        np.testing.assert_allclose(a.reach, b.reach)                   # зона Френеля высоко над крышей

    def test_own_building_ignored(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 30, "poly": rect(0, 0, 40, 40)}])   # вышка на крыше
        a = C.compute_ray_reach(LAT0, LON0, 1800, 20, 20, 20, None)
        b = C.compute_ray_reach(LAT0, LON0, 1800, 20, 20, 20, bd)
        np.testing.assert_allclose(a.reach, b.reach)

    def test_wall_loss_monotonic(self):
        bd = make_city()
        prev = None
        for wl in (0, 10, 20, 40):
            r = C.compute_ray_reach(LAT0, LON0, 1800, 10, 5, wl, bd).reach.sum()
            if prev is not None:
                self.assertLessEqual(r, prev + 1e-6)
            prev = r

    def test_vectorized_equals_reference(self):
        bd = make_city()
        for kw in (dict(freq=1800, power=10, height=5, wall_loss=20, azimuth=0, beamwidth=360),
                   dict(freq=800, power=40, height=12, wall_loss=15, azimuth=45, beamwidth=120),
                   dict(freq=2600, power=1, height=3, wall_loss=25, azimuth=200, beamwidth=360)):
            tw = Tower("t", LAT0 + 3e-4, LON0 - 2e-4, tilt=0, color=[1, 2, 3, 255], building_data=bd,
                       fresnel=False, pattern=False, **kw)                 # эталон — модель стен без дифракции
            np.testing.assert_allclose(tw.reach, reference_reach(tw, bd), atol=1e-6, err_msg=str(kw))

    def test_probe_matches_zone_boundary(self):
        bd = make_city()
        self._probe_matches(Tower("t", LAT0, LON0, 1800, 10, 5, 0, 20, [1, 2, 3, 255], building_data=bd), bd)
        sector = Tower("s", LAT0, LON0, 1800, 0.5, 12, 0, 20, [1, 2, 3, 255], building_data=bd,
                       azimuth=60, beamwidth=65)                           # лепестки + дифракция
        self._probe_matches(sector, bd)

    def _probe_matches(self, tw, bd):
        checked = 0
        for i in range(0, 180, 7):
            for z, name in enumerate(C.ZONE_NAMES):
                r = tw.reach[i, z]
                if r < 20 or r >= C.WINDOW_HALF_M - 5 or abs(r - C.max_range_cap_m(tw.freq, tw.height)) < 1:
                    continue   # у потолка дальности зонд (без потолка) и зона расходятся по построению
                az = tw.angles[i]
                for dist, expect_in in ((r - 0.5 * C.GRID_RES_M, True), (r + 2.5 * C.GRID_RES_M, False)):
                    lat = tw.lat + math.cos(math.radians(az)) * dist / C.M_PER_DEG_LAT
                    lon = tw.lon + math.sin(math.radians(az)) * dist / M_LON
                    res = C.link_analysis(tw, lat, lon, bd)
                    ok = res["rssi_dbm"] >= C.THRESHOLDS[z][1]
                    self.assertEqual(ok, expect_in, (i, name, dist, res["rssi_dbm"]))
                    checked += 1
        self.assertGreater(checked, 30)

    def test_probe_out_of_sector(self):
        tw = Tower("t", LAT0, LON0, 1800, 20, 30, 0, 20, [1, 2, 3, 255], azimuth=0, beamwidth=60, pattern=False)
        res = C.link_analysis(tw, LAT0 - 0.001, LON0)             # строго за спиной
        self.assertFalse(res["in_sector"])
        self.assertIsNone(res["rssi_dbm"])
        tw = Tower("t", LAT0, LON0, 1800, 20, 30, 0, 20, [1, 2, 3, 255], azimuth=0, beamwidth=60)
        back = C.link_analysis(tw, LAT0 - 0.001, LON0)
        front = C.link_analysis(tw, LAT0 + 0.001, LON0)
        self.assertFalse(back["in_sector"])
        self.assertAlmostEqual(front["rssi_dbm"] - back["rssi_dbm"], C.FRONT_TO_BACK_DB, places=3)

    def test_legacy_ray_tracing_format(self):
        rr = C.ray_tracing(LAT0, LON0, 1800, 10, 5, 20)
        self.assertEqual(len(rr), 720)
        self.assertEqual({r["zone"] for r in rr}, set(C.ZONE_NAMES))
        self.assertEqual(len(C.calc_radii_from_rays(rr)), 4)


class TestTowerAndDB(unittest.TestCase):
    def test_roundtrip_dict(self):
        tw = Tower("A", LAT0, LON0, 1800, 10, 5, 6, 20, [255, 0, 0, 255], building_data=make_city())
        t2 = Tower.from_dict(json.loads(json.dumps(tw.to_dict())), recalc=False)
        np.testing.assert_allclose(t2.reach, tw.reach, atol=0.06)
        self.assertEqual(t2.color, tw.color)

    def test_db_color_and_delete_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            db = TowerDB(os.path.join(d, "t.db"))
            a = Tower("Вышка #1", LAT0, LON0, 1800, 10, 5, 6, 20, [0, 0, 255, 255], type_name="X")
            b = Tower("Вышка #1", LAT0 + 1e-3, LON0, 1800, 10, 5, 6, 20, [0, 255, 0, 255])
            ia, ib = db.save_tower(a.to_dict()), db.save_tower(b.to_dict())
            db.delete_tower(ia)                                    # раньше удалялись все одноимённые
            rows = db.load_all_towers()
            self.assertEqual([r["id"] for r in rows], [ib])
            t = Tower.from_db_row(rows[0])
            self.assertEqual(t.color, [0, 255, 0, 255])
            np.testing.assert_allclose(t.reach, b.reach, atol=0.06)
            db.set_meta("zones", {"algo": [[1, 2]]})
            self.assertEqual(db.get_meta("zones"), {"algo": [[1, 2]]})
            db.close()

    def test_migration_from_old_schema(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            con = sqlite3.connect(path)
            con.execute("""CREATE TABLE towers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, lat REAL,
                lon REAL, freq REAL, power REAL, height REAL, tilt REAL, wall_loss REAL, scale REAL,
                azimuth REAL, beamwidth REAL)""")
            con.execute("INSERT INTO towers VALUES (NULL,'Старая',47.0,28.8,1800,10,5,6,20,1,0,360)")
            con.commit()
            con.close()
            db = TowerDB(path)
            t = Tower.from_db_row(db.load_all_towers()[0])
            self.assertEqual(t.name, "Старая")
            self.assertGreater(t.r_weak, 0)
            db.close()


class TestGeoCoverageExport(unittest.TestCase):
    def test_geo(self):
        self.assertAlmostEqual(geo.haversine_m(0, 0, 1, 0), 111195, delta=100)
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 0, 1), 90.0, places=3)
        lat, lon = geo.destination(47, 28, 135, 5000)
        self.assertAlmostEqual(geo.haversine_m(47, 28, lat, lon), 5000, delta=1)
        self.assertAlmostEqual(geo.bearing_deg(47, 28, lat, lon), 135, delta=0.1)
        sq = rect(0, 0, 1000, 1000)
        self.assertAlmostEqual(geo.polygon_area_m2(sq) / 1e6, 1.0, delta=0.02)
        self.assertTrue(geo.point_in_polygon(LAT0, LON0, sq))
        self.assertFalse(geo.point_in_polygon(LAT0 + 0.1, LON0, sq))
        self.assertAlmostEqual(geo.fspl_db(1000, 1800), 97.55, delta=0.1)
        self.assertEqual(geo.parse_latlon("47.0101, 28.8638"), (47.0101, 28.8638))
        self.assertEqual(geo.parse_latlon("47.0101;28.8638"), (47.0101, 28.8638))
        with self.assertRaises(ValueError):
            geo.parse_latlon("95, 10")
        with self.assertRaises(ValueError):
            geo.parse_latlon("abc")
        self.assertEqual(geo.format_distance(250), "250 м")
        self.assertEqual(geo.format_distance(1500), "1.500 км")
        self.assertIn("СВ", geo.format_bearing(45))

    def test_coverage_stats(self):
        tw = Tower("t", LAT0, LON0, 1800, 10, 5, 0, 20, [1, 2, 3, 255])
        r = tw.r_weak * 1000
        small = rect(0, 0, r, r)                    # квадрат целиком внутри зоны
        s = coverage.coverage_stats([tw], small)
        self.assertGreater(s["covered"], 0.99)
        far = rect(5000, 5000, 200, 200)
        self.assertEqual(coverage.coverage_stats([tw], far)["covered"], 0.0)
        self.assertIsNone(coverage.coverage_stats([tw], None))
        # запретная зона вырезается из статистики
        s2 = coverage.coverage_stats([tw], small, forbidden=[small])
        self.assertIsNone(s2)

    def test_exports(self):
        tw = Tower("A", LAT0, LON0, 1800, 10, 5, 6, 20, [255, 0, 0, 255])
        csv_text = exporters.towers_to_csv([tw])
        self.assertEqual(len(csv_text.strip().splitlines()), 2)
        gj = exporters.coverage_geojson([tw], algo_zone=rect(0, 0, 100, 100))
        kinds = [f["properties"]["kind"] for f in gj["features"]]
        self.assertEqual(kinds.count("coverage"), 4)
        self.assertIn("algo_zone", kinds)
        json.dumps(gj)
        proj = exporters.project_from_json(exporters.project_to_json({}, [tw], None, []))
        self.assertEqual(len(proj["towers"]), 1)
        with self.assertRaises(ValueError):
            exporters.project_from_json('{"x":1}')


class TestPerformance(unittest.TestCase):
    def test_tower_is_fast(self):
        import time
        bd = make_city(n=600, extent=900)
        ts = []
        for k in range(10):
            t = time.perf_counter()
            Tower("t", LAT0 + k * 1e-4, LON0, 800, 40, 35, 0, 15, [1, 2, 3, 255], building_data=bd)
            ts.append(time.perf_counter() - t)
        self.assertLess(sorted(ts)[len(ts) // 2], 0.05)      # медиана < 50 мс (у старого кода ~47 мс без затухания)


if __name__ == "__main__":
    unittest.main(verbosity=2)
