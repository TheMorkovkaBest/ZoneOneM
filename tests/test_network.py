try:
    import _bootstrap
except ImportError:
    pass

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geo  # noqa: E402
import netcatalog as NC  # noqa: E402
from buildings import BuildingData  # noqa: E402
from network import Network, fmt_speed  # noqa: E402
from test_core import LAT0, LON0, M_LON, rect  # noqa: E402
from calculations import M_PER_DEG_LAT  # noqa: E402


def off(net, model, name, east_m=0.0, north_m=0.0, **kw):
    return net.add_device(model, name, LAT0 + north_m / M_PER_DEG_LAT, LON0 + east_m / M_LON, **kw)


def cable(net, a, pa, b, pb, cid, length=None):
    link, err = net.connect(a.id, pa, b.id, pb, cid)
    assert link is not None, err
    link.length_m = length
    return link


class TestCatalog(unittest.TestCase):
    def test_catalog_consistent(self):
        for cid, c in NC.CABLES.items():
            self.assertEqual(c["id"], cid)
            self.assertIn(c["group"], NC.GROUP_NAMES)
            if c["group"] in ("fiber", "dac"):
                self.assertIn(c["form"], NC.PORT_ACCEPTS)
                self.assertGreater(c["max_m"], 0)
            if c["group"] == "radio":
                self.assertIn(c["band"], NC.RADIO_BANDS)
        for mid, m in NC.MODELS.items():
            self.assertIn(m["role"], NC.ROLE_NAMES)
            ports = NC.model_ports(mid)
            self.assertEqual(len({p[0] for p in ports}), len(ports), f"порты {mid} уникальны")
            for name, kind, mbps, pse, pd in ports:
                self.assertIn(kind, NC.PORT_KINDS)
            if any(p[3] for p in ports):
                self.assertGreater(m["poe_budget_w"], 0, mid)
            if m["role"] == "radio":
                self.assertIn(m["band"], NC.RADIO_BANDS)
            if m["role"] == "tower":
                self.assertIn("radio", m)

    def test_known_numbers(self):
        self.assertEqual(NC.POE_TYPES["at"][0], 30.0)
        self.assertEqual(NC.POE_TYPES["bt4"][0], 90.0)
        self.assertEqual(NC.CABLES["f_10g_lr"]["max_m"], 10000)
        self.assertEqual(NC.CABLES["f_100g_sr4"]["max_m"], 100)
        self.assertEqual(NC.default_model_for_tower(1800, 5), "bs_small")
        self.assertEqual(NC.default_model_for_tower(3500, 30), "bs_macro_5g")
        self.assertEqual(fmt_speed(2500), "2.5G")
        self.assertEqual(fmt_speed(100), "100M")


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.net = Network()
        self.sw = off(self.net, "sw_l2_24poe", "SW")
        self.agg = off(self.net, "sw_l3_agg10", "AGG", 100)

    def test_connector_rules(self):
        n, sw, agg = self.net, self.sw, self.agg
        link, err = n.connect(sw.id, "Te1/1/1", agg.id, "Te1/0/1", "cat6a")           # медь в SFP+ нельзя
        self.assertIsNone(link)
        self.assertIn("RJ45", err)
        link, err = n.connect(sw.id, "Te1/1/1", agg.id, "Hu1/1/1", "f_100g_sr4")      # QSFP28-модуль в SFP+ нельзя
        self.assertIsNone(link)
        link, err = n.connect(sw.id, "Te1/1/1", agg.id, "Hu1/1/1", "f_10g_lr")        # SFP+ в QSFP28 нельзя
        self.assertIsNone(link)
        link, err = n.connect(sw.id, "Te1/1/1", agg.id, "Te1/0/1", "f_10g_lr")
        self.assertIsNotNone(link, err)
        link, err = n.connect(sw.id, "Te1/1/1", agg.id, "Te1/0/2", "f_10g_lr")        # порт занят
        self.assertIsNone(link)
        self.assertIn("занят", err)
        link, err = n.connect(sw.id, "Te1/1/2", sw.id, "Te1/1/3", "dac_10g")
        self.assertIn("самим собой", err)
        # SFP-модуль 1G в порту SFP+ работает (обратная совместимость)
        link, err = n.connect(sw.id, "Te1/1/3", agg.id, "Te1/0/3", "f_1g_lx")
        self.assertIsNotNone(link, err)

    def test_free_ports_and_removal(self):
        n = self.net
        before = len(n.free_ports(self.sw.id))
        lk = cable(n, self.sw, "Te1/1/1", self.agg, "Te1/0/1", "f_10g_lr")
        self.assertEqual(len(n.free_ports(self.sw.id)), before - 1)
        n.remove_device(self.agg.id)
        self.assertEqual(len(n.links), 0)
        self.assertEqual(len(n.free_ports(self.sw.id)), before)


class TestLinkPhysics(unittest.TestCase):
    def setUp(self):
        self.net = Network()
        self.a = off(self.net, "sw_mgig_24bt", "A")
        self.b = off(self.net, "sw_mgig_24bt", "B", 10)

    def status(self, cid, length, pa="Mg1/0/1", pb="Mg1/0/1"):
        for l in list(self.net.links.values()):
            self.net.remove_link(l.id)
        cable(self.net, self.a, pa, self.b, pb, cid, length)
        self.net.evaluate()
        return next(iter(self.net.status.values()))

    def test_copper_speed_vs_length(self):
        s = self.status("cat6a", 100)
        self.assertEqual((s.state, s.speed_mbps), ("up", 10000))
        s = self.status("cat6", 50)
        self.assertEqual((s.state, s.speed_mbps), ("up", 10000))
        s = self.status("cat6", 70)                     # Cat6 10G только до 55 м -> согласование вниз
        self.assertEqual((s.state, s.speed_mbps), ("warn", 5000))
        s = self.status("cat5e", 80)                    # порты 10G, Cat5e даёт максимум 2.5G
        self.assertEqual((s.state, s.speed_mbps), ("warn", 2500))
        s = self.status("cat6a", 130)                   # длиннее 100 м
        self.assertEqual((s.state, s.speed_mbps), ("down", 0.0))
        self.assertIn("100", s.reason)
        s = self.status("cat8", 25)
        self.assertEqual(s.speed_mbps, 10000)           # RJ45-порты 10G — быстрее нельзя

    def test_optics_reach_and_port_limit(self):
        s = self.status("f_10g_sr3", 250, "Te1/1/1", "Te1/1/1")
        self.assertEqual((s.state, s.speed_mbps), ("up", 10000))
        s = self.status("f_10g_sr3", 350, "Te1/1/1", "Te1/1/1")
        self.assertEqual(s.state, "down")
        s = self.status("f_10g_lr", 9500, "Te1/1/1", "Te1/1/1")
        self.assertEqual(s.state, "up")
        s = self.status("f_10g_lr", 12000, "Te1/1/1", "Te1/1/1")
        self.assertEqual(s.state, "down")

    def test_25g_module_in_10g_port_denied_and_speed_limited(self):
        self.assertIsNone(self.net.connect(self.a.id, "Tw1/1/1", self.b.id, "Te1/1/1", "f_25g_sr")[0])
        link = cable(self.net, self.a, "Tw1/1/1", self.b, "Tw1/1/2", "f_10g_sr4", 100)   # 10G-модуль в SFP28
        self.net.evaluate()
        self.assertEqual(self.net.status[link.id].speed_mbps, 10000)

    def test_dac_length(self):
        s = self.status("dac_10g", 5, "Te1/1/1", "Te1/1/1")
        self.assertEqual(s.state, "up")
        s = self.status("dac_10g", 9, "Te1/1/1", "Te1/1/1")
        self.assertEqual(s.state, "down")

    def test_auto_length_from_map(self):
        link = cable(self.net, self.a, "Mg1/0/1", self.b, "Mg1/0/1", "cat6a")           # 10 м по прямой
        self.net.evaluate()
        self.assertAlmostEqual(self.net.status[link.id].length_m, 10 * 1.25 + 10, delta=1.0)


class TestRadio(unittest.TestCase):
    def build(self, band, dist_m, bd=None, hA=None, hB=None):
        net = Network()
        a = off(net, f"mw_{band}", "R1")
        b = off(net, f"mw_{band}", "R2", dist_m)
        if hA:
            a.mount_h, b.mount_h = hA, hB
        link, err = net.connect(a.id, "Air1", b.id, "Air1", f"mw_{band}")
        self.assertIsNotNone(link, err)
        net.evaluate(bd)
        return net, net.status[link.id]

    def test_ranges_by_band(self):
        _, s = self.build("trad", 20000)
        self.assertEqual(s.state, "up")
        self.assertGreaterEqual(s.speed_mbps, 1000)
        _, s = self.build("trad", 60000)
        self.assertEqual(s.state, "down")
        _, s = self.build("eband", 1500)
        self.assertEqual(s.state, "up")
        self.assertEqual(s.speed_mbps, 10000)
        _, s = self.build("eband", 4900)
        self.assertEqual(s.state, "warn")                  # доступность в дождь под вопросом
        self.assertIn("дожд", s.reason)
        _, s = self.build("eband", 7000)
        self.assertEqual(s.state, "down")
        _, s = self.build("vband", 800)
        self.assertEqual(s.state, "up")
        _, s = self.build("vband", 3000)
        self.assertEqual(s.state, "down")

    def test_line_of_sight_blocked_by_building(self):
        bd = BuildingData()
        bd.add_buildings([{"id": 1, "height": 60, "poly": rect(500, 0, 40, 60)}])
        _, s = self.build("trad", 1000, bd, 25, 25)
        self.assertEqual(s.state, "down")
        self.assertIn("видимости", s.reason)
        _, s = self.build("trad", 1000, bd, 90, 90)        # антенны выше здания
        self.assertIn(s.state, ("up", "warn"))
        _, s = self.build("trad", 1000, None, 25, 25)      # без данных о зданиях считаем, что видимость есть
        self.assertEqual(s.state, "up")

    def test_band_mismatch_denied(self):
        net = Network()
        a, b = off(net, "mw_trad", "A"), off(net, "mw_eband", "B", 500)
        link, err = net.connect(a.id, "Air1", b.id, "Air1", "mw_trad")
        self.assertIsNone(link)
        self.assertIn("диапазон", err)


class TestPoE(unittest.TestCase):
    def test_budget_and_power(self):
        net = Network()
        sw = off(net, "sw_l2_8poe", "SW")                              # бюджет 240 Вт, PoE+ 30 Вт на порт
        cells = []
        for i in range(1, 9):                                          # 8 малых сот по 25 Вт ≈ 28 Вт на порт каждая
            c = off(net, "bs_small", f"C{i}", 20 * i, power_mode="poe")
            cable(net, sw, f"Gi0/{i}", c, "Eth1", "cat6a", 30)
            cells.append(c)
        net.evaluate()
        self.assertTrue(all(net.dev_state[c.id]["powered"] for c in cells))
        self.assertAlmostEqual(net.dev_state[sw.id]["poe_used_w"], 8 * 25 * 1.12, delta=0.5)   # 224 Вт из 240
        lonely = off(net, "bs_small", "C9", 200, power_mode="poe")      # без кабеля питания нет
        net.evaluate()
        self.assertFalse(net.dev_state[lonely.id]["powered"])
        self.assertIn("PoE", net.dev_state[lonely.id]["reason"])

    def test_budget_exceeded(self):
        net = Network()
        sw = off(net, "sw_mgig_24bt", "SW")
        sw.spec  # бюджет 1440 Вт
        devs = []
        for i in range(1, 25):                                         # радио V-band 20 Вт × 24 = 24*22.4=537 — влезает
            d = off(net, "mw_vband", f"V{i}", 10 * i, power_mode="poe")
            cable(net, sw, f"Mg1/0/{i}", d, "Eth1", "cat6a", 20)
            devs.append(d)
        net.evaluate()
        self.assertTrue(all(net.dev_state[d.id]["powered"] for d in devs))
        # уменьшим бюджет искусственно
        import netcatalog
        old = netcatalog.MODELS["sw_mgig_24bt"]["poe_budget_w"]
        netcatalog.MODELS["sw_mgig_24bt"]["poe_budget_w"] = 100.0
        try:
            net.evaluate()
        finally:
            netcatalog.MODELS["sw_mgig_24bt"]["poe_budget_w"] = old
        self.assertEqual(sum(net.dev_state[d.id]["powered"] for d in devs), 4)       # 4*22.4=89.6 <= 100
        dead = next(d for d in devs if not net.dev_state[d.id]["powered"])
        link = net.port_link(dead.id, "Eth1")
        self.assertEqual(net.status[link.id].state, "down")
        self.assertIn("бюджет", net.dev_state[dead.id]["reason"])

    def test_fiber_cannot_power(self):
        net = Network()
        sw = off(net, "sw_l2_24poe", "SW")
        c = off(net, "bs_small", "C", 20, power_mode="poe")
        cable(net, sw, "Te1/1/1", c, "Te1", "f_10g_sr3", 30)
        net.evaluate()
        self.assertFalse(net.dev_state[c.id]["powered"])

    def test_pse_class_too_weak(self):
        net = Network()
        sw = off(net, "sw_l2_24poe", "SW")                              # PoE+ 30 Вт (25.5 Вт на устройстве)
        v = off(net, "bs_macro_lte", "BS", 20, power_mode="poe")        # у макро-БС нет PD-порта
        cable(net, sw, "Gi1/0/1", v, "Mgmt1", "cat6a", 20)
        net.evaluate()
        self.assertFalse(net.dev_state[v.id]["powered"])


class TestRouting(unittest.TestCase):
    def build(self):
        """БС -> L2 доступа -> CSR -> агрегация -> ядро; вторая БС на том же L2."""
        net = Network()
        d = {}
        d["bs1"] = off(net, "bs_macro_lte", "БС1", 0, 0)
        d["bs2"] = off(net, "bs_macro_lte", "БС2", 20, 0)
        d["sw"] = off(net, "sw_l2_24poe", "SW", 50, 0)
        d["csr"] = off(net, "rt_csr", "CSR", 100, 0)
        d["agg"] = off(net, "rt_agg", "AGG", 200, 0)
        d["core"] = off(net, "core_5gc", "CORE", 400, 0)
        cable(net, d["bs1"], "Te1", d["sw"], "Te1/1/1", "f_10g_sr3", 80)
        cable(net, d["bs2"], "Te1", d["sw"], "Te1/1/2", "f_10g_sr3", 80)
        cable(net, d["sw"], "Te1/1/3", d["csr"], "Te0/1", "f_10g_sr3", 100)
        cable(net, d["csr"], "Te0/2", d["agg"], "Te0/1", "f_10g_lr", 1000)
        cable(net, d["agg"], "Hu0/1", d["core"], "Hu1", "f_100g_lr4", 2000)
        net.evaluate()
        return net, d

    def test_all_links_up(self):
        net, _ = self.build()
        self.assertTrue(all(s.state == "up" for s in net.status.values()), {k: v.reason for k, v in net.status.items()})

    def test_autoconfigure_and_ping(self):
        net, d = self.build()
        info = net.autoconfigure()
        self.assertEqual(info["segments"], 3)                # сегмент БС+SW+CSR, CSR-AGG, AGG-CORE
        res = net.ping(d["bs1"].id, d["core"].id)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.hops[0], d["bs1"].id)
        self.assertEqual(res.hops[-1], d["core"].id)
        self.assertIn(d["csr"].id, res.hops)
        self.assertGreater(res.rtt_ms, 0)
        self.assertEqual(len(res.links), 4)             # БС-SW, SW-CSR, CSR-AGG, AGG-CORE
        res2 = net.ping(d["bs1"].id, d["bs2"].id)            # в одном L2-сегменте — минуя маршрутизаторы
        self.assertTrue(res2.ok, res2.reason)
        self.assertNotIn(d["csr"].id, res2.hops)
        self.assertEqual(res2.hops, [d["bs1"].id, d["sw"].id, d["bs2"].id])
        back = net.ping(d["core"].id, d["bs2"].id)
        self.assertTrue(back.ok, back.reason)

    def test_ping_failures_explained(self):
        net, d = self.build()
        res = net.ping(d["bs1"].id, d["core"].id)
        self.assertFalse(res.ok)
        self.assertIn("IP", res.reason)                      # до автонастройки адресов нет
        net.autoconfigure()
        # обрыв линка CSR–AGG: оборвём слишком длинным кабелем
        net.links[net.port_link(d["csr"].id, "Te0/2").id].length_m = 15000
        net.evaluate()
        res = net.ping(d["bs1"].id, d["core"].id)
        self.assertFalse(res.ok)
        self.assertIn("не работает", res.reason)
        net.links[net.port_link(d["csr"].id, "Te0/2").id].length_m = 1000
        net.evaluate()
        self.assertTrue(net.ping(d["bs1"].id, d["core"].id).ok)
        # убрали шлюз — ответ не вернётся / запрос не дойдёт
        d["bs1"].gateway = ""
        res = net.ping(d["bs1"].id, d["core"].id)
        self.assertFalse(res.ok)
        self.assertIn("маршрут", res.reason)

    def test_partial_path_on_failure(self):
        net, d = self.build()
        net.autoconfigure()
        net.links[net.port_link(d["csr"].id, "Te0/2").id].length_m = 15000      # CSR–AGG оборван
        net.evaluate()
        res = net.ping(d["bs1"].id, d["core"].id)
        self.assertFalse(res.ok)
        self.assertEqual(res.hops[0], d["bs1"].id)
        self.assertIn(d["csr"].id, res.hops)
        self.assertNotIn(d["core"].id, res.hops)

    def test_port_accepts_cable(self):
        net = Network()
        sw = off(net, "sw_l2_24poe", "S")
        mw = off(net, "mw_eband", "M", 100)
        rj, sfp, qsfp = sw.port("Gi1/0/1"), sw.port("Te1/1/1"), off(net, "sw_l3_core100", "C").port("Hu1/0/1")
        self.assertTrue(net.port_accepts_cable(sw, rj, NC.CABLES["cat6a"]))
        self.assertFalse(net.port_accepts_cable(sw, sfp, NC.CABLES["cat6a"]))
        self.assertTrue(net.port_accepts_cable(sw, sfp, NC.CABLES["f_1g_lx"]))
        self.assertFalse(net.port_accepts_cable(sw, sfp, NC.CABLES["f_100g_sr4"]))
        self.assertTrue(net.port_accepts_cable(sw, qsfp, NC.CABLES["f_100g_sr4"]))
        self.assertTrue(net.port_accepts_cable(mw, mw.port("Air1"), NC.CABLES["mw_eband"]))
        self.assertFalse(net.port_accepts_cable(mw, mw.port("Air1"), NC.CABLES["mw_trad"]))

    def test_manual_static_route_and_return_path(self):
        net, d = self.build()
        net.autoconfigure()
        d["core"].gateway = ""                                # у ядра нет шлюза: пинг дойдёт, ответ — нет
        d["agg"].routes = [r for r in d["agg"].routes]
        res = net.ping(d["bs1"].id, d["core"].id)
        # ядро в той же /30 с AGG -> ответ на адрес БС идёт через шлюз, которого нет
        self.assertFalse(res.ok)
        self.assertIn("Ответ не вернулся", res.reason)

    def test_non_l3_device_does_not_route(self):
        net = Network()
        a = off(net, "bs_macro_lte", "A")
        b = off(net, "bs_macro_lte", "B", 50)
        c = off(net, "core_5gc", "C", 100)
        cable(net, a, "Te1", b, "Te1", "f_10g_sr3", 50)
        net.evaluate()
        net.autoconfigure()
        res = net.ping(a.id, c.id)
        self.assertFalse(res.ok)

    def test_suggest_cable(self):
        net = Network()
        a = off(net, "sw_l2_24poe", "A")
        b = off(net, "sw_l3_agg10", "B", 8000)
        best = net.suggest_cable(a.id, b.id)
        self.assertIsNotNone(best)
        self.assertIn(best["cable"], ("f_10g_lr", "f_10g_er", "f_10g_zr"))
        self.assertEqual(best["status"].speed_mbps, 10000)
        near = off(net, "sw_l3_agg10", "N", 3)
        best = net.suggest_cable(a.id, near.id)
        self.assertEqual(best["status"].speed_mbps, 10000)
        self.assertEqual(best["cable"], "dac_10g")           # рядом — самый дешёвый DAC
        far = off(net, "sw_l3_agg10", "F", 300000)           # 300 км — никакой кабель не дотянет
        self.assertIsNone(net.suggest_cable(a.id, far.id))


class TestSerializationAndExport(unittest.TestCase):
    def test_roundtrip_and_map_and_bom(self):
        net = Network()
        a = off(net, "sw_l3_agg10", "A")
        b = off(net, "sw_l3_agg10", "B", 300)
        cable(net, a, "Te1/0/1", b, "Te1/0/1", "f_10g_lr")
        net.evaluate()
        net.autoconfigure()
        net2 = Network.from_dict(json.loads(net.to_json()))
        self.assertEqual(set(net2.devices), set(net.devices))
        self.assertEqual(net2.devices[a.id].ips, net.devices[a.id].ips)
        net2.evaluate()
        self.assertEqual(next(iter(net2.status.values())).speed_mbps, 10000)
        net3 = Network()
        c = net3.add_device("rt_csr", "x", LAT0, LON0)
        self.assertEqual(c.id, "D1")
        m = net.to_map()
        self.assertEqual(len(m["devices"]), 2)
        self.assertEqual(m["links"][0]["state"], "up")
        self.assertIn("10G", m["links"][0]["label"])
        rows, total = net.bom()
        self.assertEqual(len(rows), 2)
        self.assertGreater(total, 18000)

    def test_tower_sync(self):
        class T:
            def __init__(self, i, name, freq=1800, h=30):
                self.db_id, self.name, self.lat, self.lon, self.freq, self.height = i, name, LAT0, LON0, freq, h
        net = Network()
        net.sync_towers([T(1, "A"), T(2, "B", 3500), T(3, "C", 1800, 5)])
        self.assertEqual(net.devices["T1"].model, "bs_macro_lte")
        self.assertEqual(net.devices["T2"].model, "bs_macro_5g")
        self.assertEqual(net.devices["T3"].model, "bs_small")
        sw = off(net, "sw_l2_24poe", "S")
        cable(net, sw, "Te1/1/1", net.devices["T1"], "Te1", "f_10g_sr3", 20)
        net.sync_towers([T(2, "B2", 3500), T(3, "C", 1800, 5)])          # вышку 1 удалили
        self.assertNotIn("T1", net.devices)
        self.assertEqual(len(net.links), 0)
        self.assertEqual(net.devices["T2"].name, "B2")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDemoProject(unittest.TestCase):
    def test_demo_is_alive(self):
        import exporters
        import loadtest
        from tools_path import build_demo
        types, towers, net = build_demo()
        self.assertTrue(all(s.state == "up" for s in net.status.values()), {k: v.reason for k, v in net.status.items()})
        self.assertTrue(net.dev_state["T3"]["powered"])                 # малая сота запитана по PoE
        self.assertGreater(net.dev_state[[d for d in net.devices if net.devices[d].name == "SW-Центр"][0]]["poe_used_w"], 20)
        net.autoconfigure()
        core = next(d for d in net.devices.values() if d.role == "core")
        for t in towers:                                                  # все 4 БС достают до ядра
            res = net.ping(f"T{t.db_id}", core.id)
            self.assertTrue(res.ok, f"{t.name}: {res.reason}")
        # радиорелейная линия E-band в проекте — часть пути БС «Восток 5G»
        res = net.ping("T2", core.id)
        self.assertTrue(any(net.links[l].cable == "mw_eband" for l in res.links))
        r = loadtest.run_load_test(net, towers, None, loadtest.LoadConfig(n_users=300, steps=5, seed=2))
        self.assertEqual(r.transport_mode, "network")
        self.assertEqual(len(r.towers_table), 4)
        self.assertTrue(all(row["transport_issue"] == "" for row in r.towers_table))
        # файл проекта читается и сеть из него восстанавливается
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "demo_project.json")
        with open(path, encoding="utf-8") as fh:
            data = exporters.project_from_json(fh.read())
        net2 = Network.from_dict(data["network"])
        self.assertEqual(len(net2.links), len(net.links))
        self.assertEqual(len(data["towers"]), 4)
