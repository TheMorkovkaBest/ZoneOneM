"""Дымовой тест логики gui.py на заглушке Qt (см. qt_stub.py)."""
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
        self.tmp = tempfile.TemporaryDirectory()
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
        self.assertEqual(t.reach[45, 3], 0.0)                 # на восток (индекс 45 = 90°) сигнала нет
        self.app.on_map_event({"type": "azimuth", "id": t.db_id, "azimuth": 90})
        t = self.app.towers[0]
        self.assertEqual(len(self.app.towers), 1)
        self.assertEqual(t.azimuth, 90.0)
        self.assertGreater(t.reach[45, 3], 0.0)               # индекс 45 = 90°
        self.assertEqual(t.reach[0, 3], 0.0)                  # на север сигнала больше нет
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
