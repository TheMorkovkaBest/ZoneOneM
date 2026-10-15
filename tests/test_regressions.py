"""Регрессионные тесты к исправлениям: загрузка зданий, растеризация, сеть (кабели, импорт, пути L2)."""
import math
import os
import random
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buildings  # noqa: E402
from buildings import BuildingData  # noqa: E402
from network import Network  # noqa: E402
from test_core import LAT0, LON0, M_LON, rect  # noqa: E402
from test_network import cable, off  # noqa: E402


def brute_mask(bd, poly):
    """Прежняя (заведомо верная, но O(h·E·w)) растеризация — эталон."""
    xs, ys = bd._to_cells([p[0] for p in poly], [p[1] for p in poly])
    x0, x1 = int(math.floor(xs.min())), int(math.ceil(xs.max()))
    y0, y1 = int(math.floor(ys.min())), int(math.ceil(ys.max()))
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    px, py = x0 + np.arange(w) + 0.5, y0 + np.arange(h) + 0.5
    ex2, ey2 = np.roll(xs, -1), np.roll(ys, -1)
    valid = (ys[None, :] > py[:, None]) != (ey2[None, :] > py[:, None])
    dy = np.where(ey2 == ys, 1.0, ey2 - ys)
    xc = xs[None, :] + (py[:, None] - ys[None, :]) * ((ex2 - xs) / dy)[None, :]
    return ((valid[:, :, None] & (xc[:, :, None] > px[None, None, :])).sum(axis=1) & 1).astype(bool)


class TestRasterizer(unittest.TestCase):
    def test_scanline_matches_reference(self):
        rnd = random.Random(7)
        bd = BuildingData()
        bd._origin = (LAT0, LON0)
        for _ in range(300):
            n = rnd.randint(3, 30)
            r = rnd.uniform(3, 150)
            poly = [
                (LAT0 + r * rnd.uniform(0.3, 1) * math.sin(2 * math.pi * k / n) / 111320.0,
                 LON0 + r * rnd.uniform(0.3, 1) * math.cos(2 * math.pi * k / n) / M_LON) for k in range(n)]
            ref = brute_mask(bd, poly)
            got, _, _ = bd._building_mask({"poly": poly, "height": 10})
            if ref.any():
                self.assertTrue(np.array_equal(ref, got))

    def test_huge_building_is_cheap(self):
        bd = BuildingData()
        ring = [(LAT0 + 400 * math.sin(a) / 111320.0, LON0 + 400 * math.cos(a) / M_LON)
                for a in np.linspace(0, 2 * math.pi, 400, endpoint=False)]       # круг 800 м, 400 вершин
        t = time.perf_counter()
        bd.add_buildings([{"id": "big", "height": 20, "poly": ring}])
        self.assertLess(time.perf_counter() - t, 2.0)        # прежний алгоритм: ~1.5 млрд ячеек в памяти
        self.assertGreater(bd.heights_at([LAT0], [LON0])[0], 0)


class _Resp:
    def __init__(self, data, code=200):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


OVERPASS_OK = {"elements": [
    {"type": "node", "id": 1, "lat": LAT0, "lon": LON0},
    {"type": "node", "id": 2, "lat": LAT0, "lon": LON0 + 30 / M_LON},
    {"type": "node", "id": 3, "lat": LAT0 + 30 / 111320.0, "lon": LON0 + 30 / M_LON},
    {"type": "way", "id": 10, "nodes": [1, 2, 3, 1], "tags": {"building": "yes", "height": "40"}},
]}


class TestFetch(unittest.TestCase):
    def setUp(self):
        if buildings.requests is None:
            self.skipTest("нет модуля requests")

    def test_first_good_server_wins_and_failures_are_skipped(self):
        calls = []

        def post(url, **kw):
            calls.append((url, kw))
            if url == buildings.OVERPASS_URLS[0]:
                return _Resp({}, 504)                      # главный сервер перегружен
            return _Resp(OVERPASS_OK)
        bd = BuildingData()
        with mock.patch.object(buildings.requests, "post", side_effect=post):
            self.assertTrue(bd.fetch(LAT0, LON0, radius_m=900, need_m=600))
        self.assertTrue(bd.is_covered(LAT0, LON0, 600))
        self.assertEqual(len(bd.buildings), 1)
        self.assertNotEqual(bd._good_url, buildings.OVERPASS_URLS[0])
        self.assertTrue(all("data" in kw["data"] for _, kw in calls))   # запрос — форма data=…

    def test_partial_answer_rejected(self):
        bad = dict(OVERPASS_OK, remark="runtime error: Query timed out in \"query\" at line 1 after 25 seconds.")
        bd = BuildingData()
        with mock.patch.object(buildings.requests, "post", return_value=_Resp(bad)):
            self.assertFalse(bd.fetch(LAT0, LON0))
        self.assertFalse(bd.is_covered(LAT0, LON0, 600))     # неполный ответ не считается загрузкой района

    def test_concurrent_fetches_download_once(self):
        n = []

        def post(url, **kw):
            n.append(url)
            time.sleep(0.3)
            return _Resp(OVERPASS_OK)
        bd = BuildingData()
        with mock.patch.object(buildings.requests, "post", side_effect=post), \
                mock.patch.object(buildings, "OVERPASS_PARALLEL", 1):
            ts = [threading.Thread(target=bd.ensure_loaded, args=(LAT0, LON0)) for _ in range(3)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(n), 1)                           # курсор и вышка в одном районе — один запрос

    def test_cancel(self):
        def post(url, **kw):
            time.sleep(2.0)
            return _Resp(OVERPASS_OK)
        bd = BuildingData()
        t = time.perf_counter()
        with mock.patch.object(buildings.requests, "post", side_effect=post):
            self.assertFalse(bd.fetch(LAT0, LON0, cancel=lambda: True))
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertEqual(bd._fail_until, 0.0)                # отмена — не «нет сети»


class TestNetworkFixes(unittest.TestCase):
    def test_from_dict_counters_and_bad_links(self):
        net = Network()
        a, b = off(net, "sw_l3_agg10", "A"), off(net, "sw_l3_agg10", "B", 300)
        cable(net, a, "Te1/0/1", b, "Te1/0/1", "f_10g_lr")
        data = net.to_dict()
        data["next_link"], data["next_dev"] = 1, 1                          # устаревшие счётчики
        data["devices"][0]["legacy_field"] = 123                             # поле из другой версии
        data["links"].append(dict(data["links"][0], id="L7"))                # второй кабель в занятый порт
        data["links"].append(dict(data["links"][0], id="L8", a_port="Nope"))  # несуществующий порт
        net2 = Network.from_dict(data)
        self.assertEqual(list(net2.links), ["L1"])
        link, err = net2.connect(a.id, "Te1/0/2", b.id, "Te1/0/2", "f_10g_lr")
        self.assertIsNotNone(link, err)
        self.assertEqual(len(net2.links), 2)                  # раньше новый L1 затирал старый кабель
        self.assertNotEqual(net2.add_device("rt_csr", "x", LAT0, LON0).id, a.id)

    def test_port_index_follows_changes(self):
        net = Network()
        a, b = off(net, "sw_l3_agg10", "A"), off(net, "sw_l3_agg10", "B", 30)
        lk = cable(net, a, "Te1/0/1", b, "Te1/0/1", "f_10g_sr3")
        self.assertIs(net.port_link(a.id, "Te1/0/1"), lk)
        net.links.pop(lk.id)                                  # в обход remove_link
        self.assertIsNone(net.port_link(a.id, "Te1/0/1"))
        self.assertEqual(len(net.free_ports(a.id)), len(a.ports()))

    def test_ring_of_switches_ping_is_fast(self):
        net = Network()
        sws = [off(net, "sw_l2_48poe", f"S{i}", 30 * i) for i in range(10)]
        for i in range(10):                                   # кольцо + хорды: много простых путей
            for j in (1, 2, 3):
                k = (i + j) % 10
                free_a = [p.name for p in net.free_ports(sws[i].id) if p.kind == "RJ45"]
                free_b = [p.name for p in net.free_ports(sws[k].id) if p.kind == "RJ45"]
                cable(net, sws[i], free_a[0], sws[k], free_b[0], "cat6a", 40)
        r1, r2 = off(net, "rt_csr", "R1"), off(net, "rt_csr", "R2", 300)
        cable(net, r1, "Gi0/1", sws[0], [p.name for p in net.free_ports(sws[0].id) if p.kind == "RJ45"][0], "cat6a", 20)
        cable(net, r2, "Gi0/1", sws[5], [p.name for p in net.free_ports(sws[5].id) if p.kind == "RJ45"][0], "cat6a", 20)
        net.evaluate()
        net.autoconfigure()
        t = time.perf_counter()
        res = net.ping(r1.id, r2.id)
        self.assertTrue(res.ok, res.reason)
        lp, why = net._l2_path(r1.id, "Gi0/1", r2.id, "Gi0/4")    # порт без кабеля: пути нет
        self.assertIsNone(lp)                                  # прежний BFS перебирал все простые пути кольца
        self.assertLess(time.perf_counter() - t, 1.0)

    def test_suggest_ports_for_given_cable(self):
        net = Network()
        a, b = off(net, "rt_csr", "A"), off(net, "rt_csr", "B", 50)
        best = net.suggest_cable(a.id, b.id, cable_id="f_25g_sr")
        self.assertEqual(best["cable"], "f_25g_sr")
        self.assertTrue(best["a_port"].startswith("Tw"))       # 25G-модуль — только в порты SFP28
        self.assertEqual(best["status"].speed_mbps, 25000)

    def test_tower_model_follows_edit_without_cables(self):
        class T:
            def __init__(self, h):
                self.db_id, self.name, self.lat, self.lon, self.freq, self.height = 1, "A", LAT0, LON0, 1800, h
        net = Network()
        net.sync_towers([T(30)])
        self.assertEqual(net.devices["T1"].model, "bs_macro_lte")
        net.sync_towers([T(5)])                               # стала микросотой
        self.assertEqual(net.devices["T1"].model, "bs_small")
        sw = off(net, "sw_l2_24poe", "S", 20)
        cable(net, sw, "Gi1/0/1", net.devices["T1"], "Eth1", "cat6a", 20)
        net.sync_towers([T(30)])                              # с кабелем модель не меняем — порт бы исчез
        self.assertEqual(net.devices["T1"].model, "bs_small")

    def test_hidden_towers_keep_cables_but_not_drawn(self):
        class T:
            def __init__(self, i):
                self.db_id, self.name, self.lat, self.lon, self.freq, self.height = i, f"T{i}", LAT0, LON0, 1800, 30
        net = Network()
        net.sync_towers([T(1), T(2)])
        sw = off(net, "sw_l3_agg10", "S", 20)
        cable(net, sw, "Te1/0/1", net.devices["T1"], "Te1", "f_10g_sr3", 20)
        net.sync_towers([T(2)], keep_ids={1})                 # вышка 1 убрана с карты, но есть в БД
        self.assertIn("T1", net.devices)
        self.assertEqual(len(net.links), 1)
        m = net.to_map(visible_towers={2})
        self.assertNotIn("T1", [d["id"] for d in m["devices"]])
        self.assertEqual(m["links"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
