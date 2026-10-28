"""Дымовой тест логики gui.py на заглушке Qt (см. qt_stub.py)."""
try:
    import _bootstrap
except ImportError:
    pass

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:                                       # на машине с настоящим PyQt6 этот тест пропускаем
    import PyQt6.QtWidgets  # noqa: F401
    REAL_QT = hasattr(sys.modules["PyQt6.QtWidgets"], "QApplication") and not hasattr(
        sys.modules["PyQt6.QtWidgets"], "_own")
except ImportError:
    REAL_QT = False

import qt_stub  # noqa: E402

if not REAL_QT:
    qt_stub.install()

import json as _json  # noqa: E402,F401
from test_core import LAT0, LON0, M_LON, rect, make_city   # noqa: E402
import calculations as C  # noqa: E402


@unittest.skipIf(REAL_QT, "Настоящий PyQt6 установлен — смоук на заглушке не нужен")
class TestGuiSmoke(unittest.TestCase):
    def setUp(self):
        import tower
        import gui
        self.gui = gui
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tower.TowerDB.DEFAULT_PATH = os.path.join(self.tmp.name, "t.db")
        gui.BASE_DIR = Path(self.tmp.name)
        qt_stub.QSettings._store.clear()
        qt_stub.QMessageBox.calls.clear()
        self.app = gui.CellTower3DApp()
        city = make_city()
        self.app.building_data.add_buildings([{"id": b["id"], "height": b["height"], "poly": b["poly"]}
                                              for b in city.buildings])
        self.app.building_data._register_area(LAT0, LON0, 5000)        # без сети
        self.app.web_view.loadFinished.emit(True)

    def tearDown(self):
        self.app.db.close()                # Windows: открытый файл БД не даёт удалить временную папку
        self.tmp.cleanup()

    def js(self):
        return "\n".join(self.app.web_view.page().js)

    def place(self, dlat=0.0, dlon=0.0):
        self.app._mode_actions["place"].setChecked(True)
        self.app.on_map_event({"type": "place", "lat": LAT0 + dlat, "lon": LON0 + dlon})

    def test_startup(self):
        a = self.gui
        html = a.load_map_html()
        self.assertNotIn("__GEO_UTILS__", html)
        self.assertNotIn("__MAP_APP__", html)
        self.assertIn("zoneRing", html)
        self.assertIsNotNone(self.app.web_view.html)
        self.assertIn("api.setTowers([])", self.js())
        self.assertIn("api.setMapMode('navigate')", self.js())
        self.assertEqual(self.app.active_type_combo.items[0], "Низкая микросота")
        self.assertIn("1800", self.app.type_summary.text)

    def test_place_tower_with_buildings_and_persist(self):
        self.place()
        app = self.app
        self.assertEqual(len(app.towers), 1)
        t = app.towers[0]
        self.assertTrue(t.used_buildings)
        self.assertIsNotNone(t.db_id)
        self.assertEqual(len(app.db.load_all_towers()), 1)
        self.assertIn("api.setTowers([{", self.js())
        self.assertIn("Рассчитано за", app.statusBar().last)
        self.assertEqual(app.tower_table.rowCount(), 1)
        self.assertIn("Вышка #1", app.tower_info.text)
        # без зданий зона больше
        app.chk_buildings.setChecked(False)
        self.assertFalse(app.towers[0].used_buildings)
        free = app.towers[0].reach.sum()
        app.chk_buildings.setChecked(True)
        self.assertTrue(app.towers[0].used_buildings)
        self.assertLess(app.towers[0].reach.sum(), free)

    def test_place_ignored_outside_place_mode(self):
        self.app.on_map_event({"type": "place", "lat": LAT0, "lon": LON0})
        self.assertEqual(len(self.app.towers), 0)

    def test_restart_restores_towers_without_recalc(self):
        self.place()
        reach = self.app.towers[0].reach.copy()
        self.app.db.close()
        app2 = self.gui.CellTower3DApp()
        self.assertEqual(len(app2.towers), 1)
        self.assertEqual(app2.towers[0].color, [255, 0, 0, 255])
        self.assertLess(abs(app2.towers[0].reach - reach).max(), 0.06)
        self.assertEqual(app2.tower_counter, 2)

    def test_probe_and_measure(self):
        self.app.on_map_event({"type": "probe", "lat": LAT0, "lon": LON0})
        self.assertIn("Сначала поставьте", self.app.signal_info.text)
        self.place()
        self.app.on_map_event({"type": "probe", "lat": LAT0 + 0.0008, "lon": LON0})
        txt = self.app.signal_info.text
        self.assertIn("Вышка #1", txt)
        self.assertIn("дБм", txt)
        self.assertIn("api.setProbe(", self.js())
        self.app.on_map_event({"type": "measure", "points": [[LAT0, LON0], [LAT0, LON0 + 0.01]]})
        m = self.app.measure_info.text
        self.assertIn("Итого по ломаной", m)
        self.assertIn("В", m)                               # азимут на восток
        self.assertIn("Потери в свободном пространстве", m)

    def test_zones_stats_and_algorithm_stub(self):
        self.place()
        poly = [list(p) for p in rect(0, 0, 200, 200)]
        self.app._mode_actions["zone_algo"].setChecked(True)
        self.app.on_map_event({"type": "zone", "kind": "algo", "polygon": poly})
        self.assertEqual(self.app.zone_algo, poly)
        self.assertIn("Покрыто", self.app.stats_label.text)
        self.assertIn("api.setZones(", self.js())
        self.assertEqual(self.app.db.get_meta("zones")["algo"], poly)
        self.app.on_map_event({"type": "zone", "kind": "forbidden", "polygon": [list(p) for p in rect(0, 0, 50, 50)]})
        self.assertEqual(len(self.app.zone_forbidden), 1)
        self.app.run_algorithm()
        self.assertEqual(qt_stub.QMessageBox.calls[-1][0], "information")     # заглушка: «не реализован»
        self.app.clear_zones()
        self.assertIsNone(self.app.zone_algo)

    def test_algorithm_places_towers(self):
        import auto_placement
        auto_placement.ALGORITHMS["тест"] = lambda task: [(LAT0 + 0.001, LON0), (LAT0 - 0.001, LON0)]
        self.app.algo_combo.cur = "тест"
        self.app.zone_algo = [list(p) for p in rect(0, 0, 400, 400)]
        self.app.run_algorithm()
        self.assertEqual(len(self.app.towers), 2)
        del auto_placement.ALGORITHMS["тест"]

    def test_azimuth_from_map(self):
        self.app.active_type_combo.cur = "Секторная 1800 МГц (65°)"
        self.place()
        t = self.app.towers[0]
        self.assertEqual(t.azimuth, 0.0)
        self.assertLess(t.reach[45, 0], t.reach[0, 0])        # на восток (индекс 45 = 90°) — только боковой лепесток
        self.app.on_map_event({"type": "azimuth", "id": t.db_id, "azimuth": 90})
        t = self.app.towers[0]
        self.assertEqual(len(self.app.towers), 1)
        self.assertEqual(t.azimuth, 90.0)
        self.assertGreater(t.reach[45, 0], t.reach[0, 0])     # главный луч теперь на восток (индекс 45 = 90°)
        self.assertEqual(self.app.db.load_all_towers()[0]["azimuth"], 90.0)
        self.assertTrue(t.used_buildings)
        n = len(self.app.web_view.page().js)
        self.app.on_map_event({"type": "azimuth", "id": t.db_id, "azimuth": 90.2})   # без изменений: только сброс предпросмотра
        self.assertEqual(len(self.app.towers), 1)
        self.assertGreater(len(self.app.web_view.page().js), n)

    def test_edit_and_delete(self):
        self.place()
        app, gui = self.app, self.gui
        tid = app.towers[0].db_id

        class FakeDlg:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return True

            def get_data(self):
                return {"name": "Переименована", "lat": LAT0 + 0.0005, "lon": LON0, "type_name": None,
                        "freq": 900.0, "power": 20.0, "height": 12.0, "tilt": 3.0, "wall_loss": 15.0,
                        "azimuth": 90.0, "beamwidth": 120.0, "color": [0, 0, 255, 255]}
        orig = gui.EditTowerDialog
        gui.EditTowerDialog = FakeDlg
        try:
            app.edit_selected_tower()
        finally:
            gui.EditTowerDialog = orig
        self.assertEqual(len(app.towers), 1)                 # раньше при занятом воркере вышка терялась
        t = app.towers[0]
        self.assertEqual((t.db_id, t.name, t.freq, t.beamwidth), (tid, "Переименована", 900.0, 120.0))
        self.assertEqual(app.db.load_all_towers()[0]["name"], "Переименована")
        app.delete_selected_tower()
        self.assertEqual(len(app.towers), 0)
        self.assertEqual(len(app.db.load_all_towers()), 0)

    def test_select_from_map_and_table(self):
        self.place()
        self.place(0.001)
        app = self.app
        first = app.towers[0].db_id
        app.on_map_event({"type": "select", "id": first})
        self.assertEqual(app.selected_id, first)
        self.assertEqual(app.tower_table.sel, 0)
        self.assertTrue(app.act_edit.isEnabled())
        app.on_map_event({"type": "select", "id": None})
        self.assertFalse(app.act_delete.isEnabled())

    def test_theme_and_mode_from_js(self):
        self.app.toggle_theme()
        self.assertEqual(qt_stub.QSettings._store["theme"], "light")
        self.assertIn("api.setTheme('light')", self.js())
        self.app._mode_actions["measure"].setChecked(True)
        self.assertEqual(self.app._mode, "measure")
        self.app.on_map_event({"type": "mode", "mode": "navigate"})
        self.assertEqual(self.app._mode, "navigate")

    def test_exports_and_import(self):
        self.place()
        self.place(0.001)
        app, gui = self.app, self.gui
        d = self.tmp.name
        paths = {k: os.path.join(d, k) for k in ("p.json", "t.csv", "c.geojson")}
        app._save_dialog = lambda title, default, flt: paths[{"zonem_project.json": "p.json", "towers.csv": "t.csv",
                                                              "coverage.geojson": "c.geojson"}[default]]
        app.export_project()
        app.export_csv()
        app.export_geojson()
        self.assertEqual(len(open(paths["t.csv"], encoding="utf-8").read().strip().splitlines()), 3)
        gj = json.load(open(paths["c.geojson"], encoding="utf-8"))
        self.assertEqual(sum(f["properties"]["kind"] == "coverage" for f in gj["features"]), 8)

        class FakeBox:
            class ButtonRole:
                DestructiveRole, AcceptRole, RejectRole = 0, 1, 2

            def __init__(self, *a):
                self.btns = []

            def setWindowTitle(self, *a):
                pass

            def setText(self, *a):
                pass

            def addButton(self, text, role):
                b = (text, role)
                self.btns.append(b)
                return b

            def exec(self):
                pass

            def clickedButton(self):
                return self.btns[0]                          # «Заменить»
        class FakeFD:
            @staticmethod
            def getOpenFileName(*a):
                return paths["p.json"], ""
        ob, of = gui.QMessageBox, gui.QFileDialog
        gui.QMessageBox, gui.QFileDialog = FakeBox, FakeFD
        try:
            app.clear_map_only()
            app.import_project()
        finally:
            gui.QMessageBox, gui.QFileDialog = ob, of
        self.assertEqual(len(app.towers), 2)
        self.assertEqual(len(app.db.load_all_towers()), 2)

    def test_close_and_goto(self):
        self.app.goto_coords = self.app.goto_coords           # метод существует
        self.app.closeEvent(type("E", (), {"accept": lambda s: None, "ignore": lambda s: None})())


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipIf(REAL_QT, "Настоящий PyQt6 установлен — смоук на заглушке не нужен")
class TestGuiNetwork(unittest.TestCase):
    """Сетевая часть и тест нагрузки через ту же заглушку Qt."""

    def setUp(self):
        import tower
        import gui
        self.gui = gui
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tower.TowerDB.DEFAULT_PATH = os.path.join(self.tmp.name, "t.db")
        gui.BASE_DIR = Path(self.tmp.name)
        qt_stub.QSettings._store.clear()
        qt_stub.QMessageBox.calls.clear()
        self.app = gui.CellTower3DApp()
        self.app.web_view.loadFinished.emit(True)
        self.app.building_data._register_area(LAT0, LON0, 5000)

    def tearDown(self):
        self.app.db.close()                # Windows: открытый файл БД не даёт удалить временную папку
        self.tmp.cleanup()

    def js(self):
        return "\n".join(self.app.web_view.page().js)

    def place_tower(self, east=0.0):
        self.app._mode_actions["place"].setChecked(True)
        self.app.on_map_event({"type": "place", "lat": LAT0, "lon": LON0 + east / M_LON})

    def place_device(self, model, east, north=0.0):
        self.app.net_set_model(model, activate=True)              # переключает режим «Устройство»
        self.assertEqual(self.app._mode, "device")
        self.app.on_map_event({"type": "net_place", "lat": LAT0 + north / 111320.0, "lon": LON0 + east / M_LON})
        return max(self.app.net.devices, key=lambda d: int(d[1:]) if d[0] == "D" else -1)

    def test_startup_palette_and_ribbon(self):
        app = self.app
        self.assertEqual(app.net_cable, "auto")
        self.assertEqual(app.net_dev_model, "sw_l2_24poe")
        roles = [t.texts[0] for t in app.dev_tree.tops]
        self.assertNotIn("Базовые станции", roles)                 # БС ставятся вкладкой «Радио»
        self.assertIn("Коммутаторы L2 (доступ)", roles)
        self.assertEqual(app.cable_tree.tops[0].data(0, 0), "auto")
        self.assertIn("api.setNetwork(", self.js())
        self.assertIn("Сеть:", app.lbl_net.text)
        for m in ("device", "cable", "pdu"):
            self.assertIn(m, app._mode_actions)
        self.assertIn("Порты", app.dev_detail.text)

    def test_tower_becomes_network_device(self):
        self.place_tower()
        net = self.app.net
        tid = self.app.towers[0].db_id
        self.assertIn(f"T{tid}", net.devices)
        self.assertEqual(net.devices[f"T{tid}"].model, "bs_small")        # высота 5 м
        self.assertIn('"tower": true', self.js())

    def test_build_network_connect_ping_persist(self):
        app = self.app
        self.place_tower()
        tid = f"T{app.towers[0].db_id}"
        sw = self.place_device("sw_l2_24poe", 150)
        csr = self.place_device("rt_csr", 300)
        core = self.place_device("core_5gc", 600)
        self.assertEqual(len(app.net.devices), 4)
        self.assertEqual(app.net.devices[sw].name, "SW-1")
        for a, b in ((tid, sw), (sw, csr), (csr, core)):
            app._mode_actions["cable"].setChecked(True)
            app.on_map_event({"type": "net_connect", "a": a, "b": b})
        self.assertEqual(len(app.net.links), 3)
        self.assertTrue(all(s.state in ("up", "warn") for s in app.net.status.values()),
                        {k: v.reason for k, v in app.net.status.items()})
        self.assertIn("Сеть: устройств 3, линков 3", app.lbl_net.text)
        app.net_autoconfigure()
        self.assertTrue(app.net.devices[core].ips)
        app._mode_actions["pdu"].setChecked(True)
        app.on_map_event({"type": "net_pdu", "a": tid, "b": core})
        self.assertIn("✓", app.ping_log.text)
        self.assertIn("RTT", app.ping_log.text)
        self.assertIn("api.animate(", self.js())
        self.assertTrue(self.js().count("true, \"RTT") >= 1)
        # сеть переживает перезапуск
        app.db.close()
        app2 = self.gui.CellTower3DApp()
        self.assertEqual(len(app2.net.links), 3)
        self.assertEqual(set(app2.net.devices), set(app.net.devices))
        self.assertEqual(app2.net.devices[core].ips, app.net.devices[core].ips)

    def test_bad_cable_choice_reported(self):
        app = self.app
        self.place_tower()
        tid = f"T{app.towers[0].db_id}"
        sw = self.place_device("sw_l2_24poe", 100)
        app.net_set_cable("cat6a", activate=True)
        app.on_map_event({"type": "net_connect", "a": tid, "b": sw})              # у малой соты есть RJ45 — ок
        self.assertEqual(len(app.net.links), 1)
        app.net_set_cable("f_100g_sr4", activate=True)                              # QSFP28-оптика: портов нет
        app.on_map_event({"type": "net_connect", "a": tid, "b": sw})
        self.assertEqual(len(app.net.links), 1)
        self.assertEqual(qt_stub.QMessageBox.calls[-1][0], "warning")
        far = self.place_device("sw_l2_24poe", 400000)
        app.net_set_cable("cat6a", activate=True)
        app.on_map_event({"type": "net_connect", "a": sw, "b": far})               # 400 км медью — линк «не работает»
        link = list(app.net.links.values())[-1]
        self.assertEqual(app.net.status[link.id].state, "down")
        self.assertIn("не работает", app.net_info.text)
        app.net_set_cable("auto", activate=True)
        far2 = self.place_device("sw_l3_agg10", 500000)
        n = len(app.net.links)
        app.on_map_event({"type": "net_connect", "a": sw, "b": far2})              # 500 км — никакой кабель
        self.assertEqual(len(app.net.links), n)
        self.assertEqual(qt_stub.QMessageBox.calls[-1][0], "warning")

    def test_select_configure_delete(self):
        app, gui = self.app, self.gui
        a = self.place_device("rt_csr", 100)
        self.assertEqual(app._net_sel, ("dev", a))
        self.assertIn("Cell Site Router", app.net_info.text)
        import net_ui

        class FakeDlg:
            def __init__(self, net, dev, parent=None):
                self.dev = dev

            def exec(self):
                return True

            def apply(self):
                self.dev.name = "CSR-главный"
                self.dev.ips = {"Te0/1": "10.9.9.1/24"}
        orig = net_ui.DeviceDialog
        net_ui.DeviceDialog = FakeDlg
        try:
            app.net_configure_selected()
        finally:
            net_ui.DeviceDialog = orig
        self.assertEqual(app.net.devices[a].name, "CSR-главный")
        self.assertEqual(app.db.get_meta("network")["devices"][0]["ips"], {"Te0/1": "10.9.9.1/24"})
        app.net_delete_selected()
        self.assertNotIn(a, app.net.devices)
        self.assertEqual(app.db.get_meta("network")["devices"], [])
        # вышку отсюда не удалить
        self.place_tower()
        app.on_map_event({"type": "select", "id": app.towers[0].db_id})
        app.net_delete_selected()
        self.assertEqual(len(app.towers), 1)

    def test_move_device_updates_length(self):
        app = self.app
        a, b = self.place_device("sw_l2_24poe", 100), self.place_device("sw_l2_24poe", 200)
        app.net_set_cable("cat6a", activate=True)
        app.on_map_event({"type": "net_connect", "a": a, "b": b})
        link = next(iter(app.net.links.values()))
        before = app.net.status[link.id].length_m
        app.on_map_event({"type": "net_move", "id": b, "lat": LAT0, "lon": LON0 + 500 / M_LON})
        self.assertGreater(app.net.status[link.id].length_m, before + 100)

    def test_clear_map_keeps_network(self):
        app = self.app
        self.place_tower()
        tid = f"T{app.towers[0].db_id}"
        sw = self.place_device("sw_l2_24poe", 100)
        app.on_map_event({"type": "net_connect", "a": tid, "b": sw})
        self.assertEqual(len(app.net.links), 1)
        app.clear_map_only()                                       # вышки остаются в БД — кабели не теряем
        self.assertEqual(len(app.db.get_meta("network")["links"]), 1)
        app.clear_all()
        self.assertEqual(len(app.net.devices), 0)

    def test_load_test_radio_only_and_network(self):
        app = self.app
        app.on_map_event({"type": "net_pdu", "a": "x", "b": "y"})          # неизвестные id не ломают
        app.run_load_test()
        self.assertEqual(qt_stub.QMessageBox.calls[-1][0], "information")   # без вышек — подсказка
        self.place_tower()
        app.ld_users.setValue(200)
        app.ld_steps.setValue(4)
        app.ld_active.setValue(30)
        app.run_load_test()
        res = app._load_result
        self.assertIsNotNone(res)
        self.assertEqual(res.transport_mode, "radio_only")
        self.assertIn("Рекомендации", app.ld_summary.text)
        self.assertIn("только радио", app.ld_summary.text)
        self.assertIn("api.setTraffic(", self.js())
        self.assertIn('"linkUtil"', self.js())
        self.assertIn("Тест нагрузки:", app.statusBar().last)
        # с транспортом
        tid = f"T{app.towers[0].db_id}"
        csr = self.place_device("rt_csr", 150)
        core = self.place_device("core_5gc", 400)
        for a, b in ((tid, csr), (csr, core)):
            app._mode_actions["cable"].setChecked(True)
            app.on_map_event({"type": "net_connect", "a": a, "b": b})
        app.run_load_test()
        res = app._load_result
        self.assertEqual(res.transport_mode, "network")
        self.assertEqual(len(res.link_ids), 2)
        self.assertIn("Линки", app.ld_summary.text)
        app._ld_select_step(1)
        self.assertIn("шаг 2/4", app.ld_step_lbl.text)
        app.clear_load_results()
        self.assertIn("api.clearTraffic()", self.js())

    def test_project_export_import_keeps_network(self):
        app, gui = self.app, self.gui
        self.place_tower()
        tid = f"T{app.towers[0].db_id}"
        sw = self.place_device("sw_l2_24poe", 120)
        app.on_map_event({"type": "net_connect", "a": tid, "b": sw})
        path = os.path.join(self.tmp.name, "p.json")
        app._save_dialog = lambda *a: path
        app.export_project()
        data = json.load(open(path, encoding="utf-8"))
        self.assertEqual(len(data["network"]["links"]), 1)

        class FakeBox:
            class ButtonRole:
                DestructiveRole, AcceptRole, RejectRole = 0, 1, 2

            def __init__(self, *a):
                self.btns = []

            def setWindowTitle(self, *a):
                pass

            def setText(self, *a):
                pass

            def addButton(self, text, role):
                self.btns.append((text, role))
                return self.btns[-1]

            def exec(self):
                pass

            def clickedButton(self):
                return self.btns[0]

        class FakeFD:
            @staticmethod
            def getOpenFileName(*a):
                return path, ""
        ob, of = gui.QMessageBox, gui.QFileDialog
        gui.QMessageBox, gui.QFileDialog = FakeBox, FakeFD
        try:
            app.import_project()
        finally:
            gui.QMessageBox, gui.QFileDialog = ob, of
        self.assertEqual(len(app.towers), 1)
        new_tid = f"T{app.towers[0].db_id}"
        self.assertEqual(len(app.net.links), 1)
        link = next(iter(app.net.links.values()))
        self.assertIn(new_tid, (link.a_dev, link.b_dev))              # связь перенесена на новую вышку
        self.assertEqual(app.net.devices[new_tid].tower_id, app.towers[0].db_id)


@unittest.skipIf(REAL_QT, "Настоящий PyQt6 установлен — смоук на заглушке не нужен")
class TestGuiRegressions(unittest.TestCase):
    """Первая вышка без ожидания сети, очередь расчётов, кабели после «Очистить карту», импорт сети."""

    def setUp(self):
        import tower
        import gui
        self.gui = gui
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tower.TowerDB.DEFAULT_PATH = os.path.join(self.tmp.name, "t.db")
        gui.BASE_DIR = Path(self.tmp.name)
        qt_stub.QSettings._store.clear()
        qt_stub.QMessageBox.calls.clear()
        self.app = gui.CellTower3DApp()
        self.app.web_view.loadFinished.emit(True)

    def tearDown(self):
        self.app.db.close()
        self.tmp.cleanup()

    def place(self, east=0.0):
        self.app._mode_actions["place"].setChecked(True)
        self.app.on_map_event({"type": "place", "lat": LAT0, "lon": LON0 + east / M_LON})

    def test_first_tower_does_not_wait_for_network(self):
        app, city = self.app, make_city()
        events = []

        def slow_fetch(lat, lon, need_m=600.0, radius_m=900, cancel=None):
            events.append(("fetch", len(app.towers)))          # к началу загрузки вышка уже на карте
            app.building_data.add_buildings([{"id": b["id"], "height": b["height"], "poly": b["poly"]}
                                             for b in city.buildings])
            app.building_data._register_area(LAT0, LON0, 5000)
            return True
        app.building_data.ensure_loaded = slow_fetch
        self.place()
        self.assertEqual(events, [("fetch", 1)])
        t = app.towers[0]
        self.assertTrue(t.used_buildings)                         # после загрузки — пересчитана со зданиями
        self.assertFalse(t.buildings_pending)
        self.assertEqual(len(app.db.load_all_towers()), 1)        # не задвоилась

    def test_network_failure_keeps_tower_and_reports(self):
        app = self.app
        app.building_data.ensure_loaded = lambda *a, **k: False
        self.place()
        self.assertEqual(len(app.towers), 1)
        self.assertTrue(app.towers[0].buildings_pending)
        self.assertIn("НЕТ СЕТИ", app.statusBar().last)

    def test_queue_does_not_stall_while_thread_finishing(self):
        app, gui = self.app, self.gui
        app.building_data._register_area(LAT0, LON0, 5000)
        orig = gui.CalcWorker.isRunning
        gui.CalcWorker.isRunning = lambda self: True              # «готово» пришло, поток ещё не вышел из run()
        first = app._on_tower_done
        state = {"n": 0}

        def on_done(tower, job):
            first(tower, job)
            state["n"] += 1
            if state["n"] == 1:                                   # пользователь кликнул второй раз во время расчёта
                self.place(300)
        app._on_tower_done = on_done
        try:
            self.place()
        finally:
            gui.CalcWorker.isRunning = orig
        self.assertEqual(len(app.towers), 2)

    def test_clear_map_then_new_tower_keeps_old_cables(self):
        app = self.app
        app.building_data._register_area(LAT0, LON0, 5000)
        self.place()
        tid = f"T{app.towers[0].db_id}"
        app.net_set_model("sw_l2_24poe", activate=True)
        app.on_map_event({"type": "net_place", "lat": LAT0, "lon": LON0 + 100 / M_LON})
        sw = next(d for d in app.net.devices if d.startswith("D"))
        app.on_map_event({"type": "net_connect", "a": tid, "b": sw})
        self.assertEqual(len(app.net.links), 1)
        app.clear_map_only()
        self.place(500)                                           # новая вышка: раньше sync удалял кабели старой
        self.assertEqual(len(app.net.links), 1)
        self.assertIn(tid, app.net.devices)
        js = next(j for j in reversed(app.web_view.page().js) if "setNetwork" in j)
        self.assertNotIn(f'"id": "{tid}"', js)                    # невидимую БС на карту не отдаём

    def test_import_maps_network_by_tower_id_not_name(self):
        app, gui = self.app, self.gui
        app.building_data._register_area(LAT0, LON0, 5000)
        self.place()
        self.place(400)
        for t in app.towers:                                      # одинаковые имена у разных вышек
            t.name = "Вышка"
        app.net_after_towers_changed()
        t1, t2 = (f"T{t.db_id}" for t in app.towers)
        app.net_set_model("sw_l3_agg10", activate=True)
        app.on_map_event({"type": "net_place", "lat": LAT0, "lon": LON0 + 600 / M_LON})
        sw = next(d for d in app.net.devices if d.startswith("D"))
        app.on_map_event({"type": "net_connect", "a": t2, "b": sw})
        path = os.path.join(self.tmp.name, "p.json")
        app._save_dialog = lambda *a: path
        app.export_project()

        class FakeBox:
            class ButtonRole:
                DestructiveRole, AcceptRole, RejectRole = 0, 1, 2

            def __init__(self, *a):
                self.btns = []

            def setWindowTitle(self, *a):
                pass

            def setText(self, *a):
                pass

            def addButton(self, text, role):
                self.btns.append((text, role))
                return self.btns[-1]

            def exec(self):
                pass

            def clickedButton(self):
                return self.btns[0]

        class FakeFD:
            @staticmethod
            def getOpenFileName(*a):
                return path, ""
        ob, of = gui.QMessageBox, gui.QFileDialog
        gui.QMessageBox, gui.QFileDialog = FakeBox, FakeFD
        try:
            app.import_project()
        finally:
            gui.QMessageBox, gui.QFileDialog = ob, of
        link = next(iter(app.net.links.values()))
        second = f"T{app.towers[1].db_id}"                        # кабель был у второй вышки
        self.assertIn(second, (link.a_dev, link.b_dev))


@unittest.skipIf(REAL_QT, "Настоящий PyQt6 установлен — смоук на заглушке не нужен")
class TestGuiPerfAndSnmp(unittest.TestCase):
    """Без лишних пересчётов при перезапуске, перенос вышки мышью, SNMP по двойному клику."""

    def setUp(self):
        import tower
        import gui
        self.gui = gui
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tower.TowerDB.DEFAULT_PATH = os.path.join(self.tmp.name, "t.db")
        gui.BASE_DIR = Path(self.tmp.name)
        qt_stub.QSettings._store.clear()
        qt_stub.QMessageBox.calls.clear()
        self.app = self._new_app()

    def _new_app(self):
        app = self.gui.CellTower3DApp()
        app.web_view.loadFinished.emit(True)
        app.building_data._register_area(LAT0, LON0, 5000)
        return app

    def tearDown(self):
        self.app.db.close()
        self.tmp.cleanup()

    def place(self, east=0.0):
        self.app._mode_actions["place"].setChecked(True)
        self.app.on_map_event({"type": "place", "lat": LAT0, "lon": LON0 + east / M_LON})

    def test_restart_does_not_recalculate_fresh_towers(self):
        self.place()
        self.place(300)
        self.app.db.close()
        app2 = self._new_app()
        calls = []
        app2._enqueue = lambda jobs: calls.append(len(jobs))
        app2._on_cache_loaded(0)
        self.assertEqual(calls, [])                                    # зоны из БД актуальны — расчёта нет
        app2.chk_pattern.on = False                                    # другая настройка модели — пересчёт
        app2._on_cache_loaded(0)
        self.assertEqual(calls, [2])
        app2.db.close()

    def test_rays_sent_only_when_changed(self):
        self.place()
        self.place(300)
        js = self.app.web_view.page().js
        self.app._push_towers()
        last = next(j for j in reversed(js) if j.startswith("api.setTowers("))
        self.assertEqual(last.count('"rays": null'), 2)                # ничего не менялось — лучи не пересылаются
        self.app.on_map_event({"type": "tower_move", "id": self.app.towers[0].db_id,
                               "lat": LAT0 + 0.001, "lon": LON0})
        last = next(j for j in reversed(js) if j.startswith("api.setTowers("))
        self.assertEqual(last.count('"rays": null'), 1)                # только перенесённая вышка

    def test_tower_drag_moves_tower_and_its_cables(self):
        app = self.app
        self.place()
        t = app.towers[0]
        app.net_set_model("sw_l2_24poe", activate=True)
        app.on_map_event({"type": "net_place", "lat": LAT0, "lon": LON0 + 150 / M_LON})
        sw = next(d for d in app.net.devices if d.startswith("D"))
        app.on_map_event({"type": "net_connect", "a": f"T{t.db_id}", "b": sw})
        before = next(iter(app.net.status.values())).length_m
        app.on_map_event({"type": "tower_move", "id": t.db_id, "lat": LAT0, "lon": LON0 - 200 / M_LON})
        t2 = app.towers[0]
        self.assertEqual(t2.db_id, t.db_id)
        self.assertAlmostEqual(t2.lon, LON0 - 200 / M_LON)
        self.assertEqual(app.db.load_all_towers()[0]["lon"], t2.lon)
        self.assertAlmostEqual(app.net.devices[f"T{t.db_id}"].lon, t2.lon)
        self.assertGreater(next(iter(app.net.status.values())).length_m, before + 200)   # кабель удлинился

    def test_snmp_from_double_click(self):
        app = self.app
        self.place()
        tid = app.towers[0].db_id
        app.on_map_event({"type": "snmp", "tower": tid})
        r = app._last_snmp
        self.assertEqual(r.device_id, f"T{tid}")
        self.assertIn("craft", r.manager)                             # нет ядра/NMS — локальное подключение
        self.assertEqual(r.extra_title, "Радио БС")
        self.assertTrue(any("ranFrequencyMHz" in n for _, n, _ in r.extra))
        app.net_set_model("core_5gc", activate=True)
        app.on_map_event({"type": "net_place", "lat": LAT0, "lon": LON0 + 200 / M_LON})
        core = next(d for d in app.net.devices if d.startswith("D"))
        app.on_map_event({"type": "net_connect", "a": f"T{tid}", "b": core})
        app.net_autoconfigure()
        app.on_map_event({"type": "snmp", "dev": f"T{tid}"})
        self.assertEqual(app._last_snmp.transport, "network", app._last_snmp.status)
        app.on_map_event({"type": "select", "id": None})
        app._net_sel = None
        n = len(qt_stub.QMessageBox.calls)
        app.snmp_selected()                                           # ничего не выбрано — подсказка
        self.assertEqual(len(qt_stub.QMessageBox.calls), n + 1)

    def test_device_drag_saved_later_and_on_close(self):
        app = self.app
        app.net_set_model("sw_l2_24poe", activate=True)
        app.on_map_event({"type": "net_place", "lat": LAT0, "lon": LON0})
        d = next(iter(app.net.devices))
        app.on_map_event({"type": "net_move", "id": d, "lat": LAT0 + 0.001, "lon": LON0})
        self.assertTrue(app._persist_due)                             # запись в БД отложена (не на каждое движение)
        app.closeEvent(type("E", (), {"accept": lambda s: None, "ignore": lambda s: None})())
        app.db = __import__("tower").TowerDB()
        saved = app.db.get_meta("network")["devices"][0]
        self.assertAlmostEqual(saved["lat"], LAT0 + 0.001)
