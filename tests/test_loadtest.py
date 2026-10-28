try:
    import _bootstrap
except ImportError:
    pass

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loadtest as LT  # noqa: E402
from buildings import BuildingData  # noqa: E402
from network import Network  # noqa: E402
from test_core import LAT0, LON0, M_LON, make_city, rect  # noqa: E402
from calculations import M_PER_DEG_LAT  # noqa: E402
from tower import Tower  # noqa: E402


def tower(i, east=0.0, north=0.0, freq=1800, power=20, height=30, bd=None, **kw):
    return Tower(f"Вышка #{i}", LAT0 + north / M_PER_DEG_LAT, LON0 + east / M_LON, freq, power, height, 0, 20,
                 [255, 0, 0, 255], building_data=bd, db_id=i, **kw)


def dev(net, model, name, east=0.0):
    return net.add_device(model, name, LAT0, LON0 + east / M_LON)


def chain(net, towers, uplink_cable="f_10g_sr3", uplink_len=50.0, core_cable="f_25g_lr"):
    """БС -> CSR -> CORE (линк БС–CSR задан кабелем uplink_cable, общий линк CSR–ядро — core_cable)."""
    net.sync_towers(towers)
    csr = dev(net, "rt_csr", "CSR", 100)
    core = dev(net, "core_5gc", "CORE", 300)
    for i, t in enumerate(towers, 1):
        link, err = net.connect(f"T{t.db_id}", "Te1", csr.id, f"Te0/{i}", uplink_cable)
        assert link, err
        link.length_m = uplink_len
    link, err = net.connect(csr.id, "Tw0/1", core.id, "Tw1", core_cable)
    assert link, err
    link.length_m = 500
    net.evaluate()
    return csr, core


class TestRadioPieces(unittest.TestCase):
    def test_rsrp_matches_zone_boundaries(self):
        t = tower(1, power=10, height=30)                     # без упора в потолок дальности зоны различимы
        r = t.reach[0]                                        # север (ось 0°)
        self.assertTrue(r[0] < r[1] < r[2] < r[3])
        pts = [(0.5, -40, -55), (r[0], -55.5, -54.5), (r[1], -70.5, -69.5), (r[2], -85.5, -84.5), (r[3], -105.5, -104.5)]
        for d, lo, hi in pts[1:]:
            lat = LAT0 + d / M_PER_DEG_LAT
            v = LT.rsrp_matrix([t], [lat], [LON0])[0, 0]
            self.assertTrue(lo <= v <= hi, (d, v))
        far = LT.rsrp_matrix([t], [LAT0 + (r[3] * 2) / M_PER_DEG_LAT], [LON0])[0, 0]
        self.assertLess(far, -115)
        near = LT.rsrp_matrix([t], [LAT0 + 1 / M_PER_DEG_LAT], [LON0])[0, 0]
        self.assertGreater(near, -56)

    def test_sector_gives_no_signal_behind(self):
        t = tower(1, azimuth=0.0, beamwidth=60)
        back, front = LT.rsrp_matrix([t], [LAT0 - 0.001, LAT0 + 0.001], [LON0, LON0])[:, 0]
        self.assertLess(back, front - 15)                     # за спиной — только задний лепесток (−25 дБ)

    def test_waterfill(self):
        s, u = LT.waterfill(np.array([1.0, 2.0]), np.array([100.0, 100.0]))
        np.testing.assert_allclose(s, [1, 2])
        self.assertAlmostEqual(u, 0.03)
        # перегрузка: ресурс делится поровну, но малый запрос удовлетворяется полностью
        s, u = LT.waterfill(np.array([1.0, 100.0, 100.0]), np.array([100.0, 100.0, 100.0]))
        self.assertAlmostEqual(s[0], 1.0)
        self.assertAlmostEqual(s[1], s[2])
        self.assertAlmostEqual(u, 1.0)
        self.assertAlmostEqual(s.sum(), 100.0)
        # разные скорости: равные доли времени => разное количество Мбит/с
        s, u = LT.waterfill(np.array([500.0, 500.0]), np.array([100.0, 20.0]))
        self.assertAlmostEqual(s[0] / s[1], 5.0)

    def test_maxmin(self):
        a = LT.maxmin(np.array([3.0, 20.0]), [[0], [0]], np.array([10.0]))
        np.testing.assert_allclose(a, [3.0, 7.0])
        a = LT.maxmin(np.array([20.0, 20.0, 20.0]), [[0, 1], [0], [1]], np.array([10.0, 12.0]))
        self.assertAlmostEqual(a[0], 5.0)                      # делит оба линка
        self.assertAlmostEqual(a[1], 5.0)
        self.assertAlmostEqual(a[2], 7.0)
        self.assertLessEqual(a[0] + a[1], 10.0 + 1e-9)
        a = LT.maxmin(np.array([5.0]), [[]], np.array([1.0]))     # без пути — не ограничен
        self.assertEqual(a[0], 5.0)

    def test_interference_lowers_rate(self):
        a, b = tower(1, 0, 0), tower(2, 250, 0)
        pos = ([LAT0], [LON0 + 100 / M_LON])
        radios = [{"tech": "LTE", "bw_mhz": 20.0, "layers": 2, "max_users": 100}] * 2
        r = LT.rsrp_matrix([a, b], *pos)
        _, rate_same = LT.sinr_and_rate(r, [a, b], radios)
        b2 = tower(2, 250, 0, freq=2600)
        r2 = LT.rsrp_matrix([a, b2], *pos)
        _, rate_diff = LT.sinr_and_rate(r2, [a, b2], radios)
        self.assertGreater(rate_diff[0, 0], rate_same[0, 0])  # разные частоты — нет интерференции


class TestRun(unittest.TestCase):
    def cfg(self, **kw):
        base = dict(n_users=400, steps=6, active_share=0.5, seed=3, scenario="city")
        base.update(kw)
        return LT.LoadConfig(**base)

    def test_radio_only_mode_without_core(self):
        towers = [tower(1, -200), tower(2, 200)]
        res = LT.run_load_test(None, towers, None, self.cfg())
        self.assertEqual(res.transport_mode, "radio_only")
        s = res.summary
        self.assertGreater(s["served_mean"], 0)
        self.assertLessEqual(s["served_mean"], s["offered_mean"] + 1e-6)
        self.assertEqual(res.link_ids, [])
        st = res.steps[0]
        self.assertEqual(int((st.state != LT.STATE_IDLE).sum()), st.active)
        self.assertEqual(len(res.towers_table), 2)
        net = Network()
        net.sync_towers(towers)                                # сеть без ядра — тоже только радио
        net.evaluate()
        self.assertEqual(LT.run_load_test(net, towers, None, self.cfg()).transport_mode, "radio_only")

    def test_deterministic_by_seed(self):
        towers = [tower(1)]
        light = dict(n_users=100, active_share=0.2)           # без перегрузки соты (иначе всегда упор в потолок)
        a = LT.run_load_test(None, towers, None, self.cfg(**light))
        b = LT.run_load_test(None, towers, None, self.cfg(**light))
        self.assertEqual(a.summary["served_mean"], b.summary["served_mean"])
        c = LT.run_load_test(None, towers, None, self.cfg(seed=99, **light))
        self.assertNotEqual(a.summary["served_mean"], c.summary["served_mean"])

    def test_transport_bottleneck_detected_and_fixed(self):
        freqs = (1800, 2600, 800)                                           # разные несущие: меньше интерференции
        towers = [tower(i + 1, -700 + 200 * i, 0, freq=freqs[i % 3]) for i in range(8)]
        fast_net, slow_net = Network(), Network()
        chain(fast_net, towers, "f_10g_sr3", 50, core_cable="f_25g_lr")      # общий линк до ядра 25G
        chain(slow_net, towers, "f_10g_sr3", 50, core_cable="f_1g_lx")       # тот же — но 1G
        for n_ in (fast_net, slow_net):                                     # мощное радио: упираться должны в транспорт
            for t_ in towers:
                n_.devices[f"T{t_.db_id}"].radio = {"tech": "NR", "bw_mhz": 100.0, "layers": 4, "max_users": 900}
        cfg = self.cfg(n_users=2000, active_share=0.8, scenario="event")
        fast = LT.run_load_test(fast_net, towers, None, cfg)
        slow = LT.run_load_test(slow_net, towers, None, cfg)
        self.assertEqual(fast.transport_mode, "network")
        self.assertLessEqual(slow.summary["served_mean"], 1000 * 0.94 + 1e-6)         # всё упирается в 1G
        self.assertGreater(fast.summary["served_mean"], slow.summary["served_mean"] * 1.2)
        worst = max(slow.links_table, key=lambda r: r["util_peak"])
        self.assertGreater(worst["util_peak"], 0.95)
        self.assertIn("CSR", worst["label"])
        self.assertTrue(any("загрузка" in r for r in slow.recommendations), slow.recommendations)
        self.assertFalse(any("CSR ↔ CORE" in r and "загрузка" in r for r in fast.recommendations))
        self.assertGreater(slow.summary["lat_mean"], 0)
        # справедливость: каждая сота получила свою долю общего канала
        shares = [row["served_peak"] for row in slow.towers_table]
        self.assertGreater(min(shares), 0)
        self.assertLess(max(shares) / max(min(shares), 1e-9), 3.0)

    def test_tower_without_path_is_blocked(self):
        towers = [tower(1, -100), tower(2, 100)]
        net = Network()
        net.sync_towers(towers)
        csr = dev(net, "rt_csr", "CSR", 100)
        core = dev(net, "core_5gc", "CORE", 300)
        link, _ = net.connect("T1", "Te1", csr.id, "Te0/1", "f_10g_sr3")
        link.length_m = 50
        link, _ = net.connect(csr.id, "Tw0/1", core.id, "Tw1", "f_25g_lr")
        link.length_m = 500
        net.evaluate()                                         # вышка 2 ни к чему не подключена
        res = LT.run_load_test(net, towers, None, self.cfg(n_users=500))
        self.assertGreater(res.summary["blocked_transport"], 0)
        row = next(r for r in res.towers_table if r["name"] == "Вышка #2")
        self.assertIn("нет физического пути", row["transport_issue"])
        self.assertTrue(any("Вышка #2" in r and "транспорта" in r for r in res.recommendations))

    def test_require_ip_needs_routing(self):
        towers = [tower(1)]
        net = Network()
        csr, core = chain(net, towers)
        cfg = self.cfg(require_ip=True)
        no_ip = LT.run_load_test(net, towers, None, cfg)
        self.assertGreater(no_ip.summary["blocked_transport"], 0)         # без IP маршрута нет
        net.autoconfigure()
        with_ip = LT.run_load_test(net, towers, None, cfg)
        self.assertEqual(with_ip.summary["blocked_transport"], 0)
        self.assertGreater(with_ip.summary["served_mean"], 0)

    def test_cell_user_limit(self):
        t = tower(1)
        net = Network()
        net.sync_towers([t])
        net.devices["T1"].radio["max_users"] = 5
        res = LT.run_load_test(net, [t], None, self.cfg(n_users=300, active_share=0.8))
        self.assertGreater(res.summary["blocked_cap"], 50)
        self.assertLessEqual(max(s.tower_users[0] for s in res.steps), 5)

    def test_users_outside_coverage(self):
        t = tower(1, power=1, height=5)
        far = rect(8000, 8000, 300, 300)
        res = LT.run_load_test(None, [t], None, self.cfg(area=far, n_users=100, active_share=1.0))
        self.assertGreater(res.summary["blocked_cov"], 90)
        self.assertEqual(res.summary["served_mean"], 0.0)
        self.assertTrue(any("вне покрытия" in r for r in res.recommendations))

    def test_indoor_share_with_buildings(self):
        bd = make_city(n=120, extent=400)
        t = tower(1, bd=bd)
        area = rect(0, 0, 800, 800)
        res = LT.run_load_test(None, [t], bd, self.cfg(n_users=300, indoor_share=0.7, hotspots=False, area=area))
        self.assertGreater(res.indoor.mean(), 0.5)
        res0 = LT.run_load_test(None, [t], None, self.cfg(n_users=300))
        self.assertEqual(res0.indoor.mean(), 0.0)

    def test_radio_overload_by_users(self):
        t = tower(1, height=5, power=10)
        light = LT.run_load_test(None, [t], None, self.cfg(n_users=100, active_share=0.2))
        heavy = LT.run_load_test(None, [t], None, self.cfg(n_users=2500, active_share=0.9, scenario="event"))
        self.assertGreater(heavy.summary["undelivered_pct"], light.summary["undelivered_pct"] + 10)
        self.assertGreater(heavy.towers_table[0]["radio_util_peak"], 0.95)
        self.assertTrue(any("радиоресурс" in r for r in heavy.recommendations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
