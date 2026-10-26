import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                             QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                             QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                             QTextBrowser, QToolBar, QToolButton, QVBoxLayout, QWidget)

import auto_placement
import coverage
import exporters
import geo
import theme as theming
import netcatalog
from buildings import BuildingData
from calculations import FRONT_TO_BACK_DB, THRESHOLDS, ZONE_LABELS_RU, link_analysis
from dialogs import (CalculatorDialog, CreateTypeDialog, EditTowerDialog, LinkProfileDialog,
                     QUALITY_COLORS)
from net_ui import NetworkMixin
from network import Network
from ribbon import RibbonBar
from tower import COLOR_MAP, DEFAULT_PRESETS, PARAM_KEYS, Tower, TowerDB

APP_TITLE = "ZoneM — планирование радиопокрытия"
BASE_DIR = Path(__file__).resolve().parent
RES_DIR = BASE_DIR / "resources"


def load_map_html():
    """Собирает страницу карты: шаблон + geo_utils.js + map_app.js."""
    html = (RES_DIR / "map.html").read_text(encoding="utf-8")
    html = html.replace("/*__GEO_UTILS__*/", (RES_DIR / "geo_utils.js").read_text(encoding="utf-8"))
    return html.replace("/*__MAP_APP__*/", (RES_DIR / "map_app.js").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  Фоновые потоки
# --------------------------------------------------------------------------- #
BUILDINGS_NEED_M = 600.0          # радиус, в котором для расчёта вышки нужны здания (= WINDOW_HALF_M)


class BuildingFetchWorker(QThread):
    """Докачивает здания OSM для списка точек (вышки, курсор). Расчёт вышек её не ждёт."""
    area_done = pyqtSignal(bool)                 # успех загрузки очередной точки

    def __init__(self, points, building_data):
        super().__init__()
        self.points, self.building_data = points, building_data
        self.cancelled = False

    def run(self):
        for lat, lon in self.points:
            if self.cancelled:
                break
            ok = self.building_data.ensure_loaded(lat, lon, need_m=BUILDINGS_NEED_M,
                                                  cancel=lambda: self.cancelled)
            if not self.cancelled:
                self.area_done.emit(ok)


class CacheWorker(QThread):
    """Чтение дискового кэша зданий при старте."""
    done = pyqtSignal(int)

    def __init__(self, building_data):
        super().__init__()
        self.building_data = building_data

    def run(self):
        self.done.emit(self.building_data.load_cache())


class CalcWorker(QThread):
    """Считает пачку вышек по уже загруженным зданиям — миллисекунды на вышку.

    Раньше здесь же качались здания (Overpass отвечает 20–40 с), и первая вышка появлялась на карте
    только после загрузки. Теперь вышка считается сразу; если зданий вокруг ещё нет, она помечается
    buildings_pending, а главное окно докачивает район и пересчитывает её.
    """
    progress = pyqtSignal(str)
    tower_done = pyqtSignal(object, object)      # Tower, job
    all_done = pyqtSignal(dict)

    def __init__(self, jobs, building_data, use_buildings, fresnel=True, pattern=True):
        super().__init__()
        self.jobs, self.building_data, self.use_buildings = jobs, building_data, use_buildings
        self.fresnel, self.pattern = fresnel, pattern

    def run(self):
        t0 = time.perf_counter()
        calc_ms, pending, errors = 0.0, 0, []
        bd = self.building_data if self.use_buildings else None
        for i, job in enumerate(self.jobs):
            try:
                missing = bd is not None and not bd.is_covered(job["lat"], job["lon"], BUILDINGS_NEED_M)
                p = job["params"]
                tower = Tower(job["name"], job["lat"], job["lon"], p["freq"], p["power"], p["height"], p["tilt"],
                              p["wall_loss"], job["color"], scale=job.get("scale", 1.0), building_data=bd,
                              azimuth=p["azimuth"], beamwidth=p["beamwidth"],
                              type_name=job.get("type_name"), db_id=job.get("db_id"),
                              fresnel=self.fresnel, pattern=self.pattern,
                              mount=job.get("mount"))
            except Exception as exc:              # одна битая вышка не должна останавливать очередь
                errors.append(f"{job.get('name')}: {type(exc).__name__}: {exc}")
                continue
            tower.buildings_pending = missing
            pending += missing
            calc_ms += tower.calc_ms
            self.tower_done.emit(tower, job)
            if len(self.jobs) > 1:
                self.progress.emit(f"Расчёт вышек: {i + 1}/{len(self.jobs)}")
        self.all_done.emit({"count": len(self.jobs), "pending": pending, "errors": errors,
                            "calc_ms": calc_ms, "total_ms": (time.perf_counter() - t0) * 1000.0})


class AlgoWorker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str, bool)               # сообщение, «не реализован»

    def __init__(self, func, task):
        super().__init__()
        self.func, self.task = func, task

    def run(self):
        try:
            self.done.emit(self.func(self.task))
        except NotImplementedError as exc:
            self.failed.emit(str(exc), True)
        except Exception as exc:                  # ошибка в алгоритме не должна ронять программу
            self.failed.emit(f"{type(exc).__name__}: {exc}", False)


# Известный безобидный шум в консоли браузера: не засоряем вывод.
_JS_NOISE = ("loaders.gl: The __VERSION__ variable",
             "Expected value to be of type number, but found null")


class MapPage(QWebEnginePage):
    """Принимает сообщения карты: console.log("PY:{json}")."""

    def __init__(self, app, profile, parent):
        super().__init__(profile, parent)
        self.app = app

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith("PY:"):
            try:
                payload = json.loads(message[3:])
            except ValueError:
                return
            self.app.on_map_event(payload)
        elif (level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel
              and not any(n in message for n in _JS_NOISE)):
            print(f"[map js] {message} ({source}:{line})", file=sys.stderr)


# --------------------------------------------------------------------------- #
#  Главное окно
# --------------------------------------------------------------------------- #
class CellTower3DApp(NetworkMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1560, 940)

        self.settings = QSettings("ZoneM", "ZoneOneM")
        self.theme_name = self.settings.value("theme", "dark")
        if self.theme_name not in theming.THEMES:
            self.theme_name = "dark"
        self.base_name = self.settings.value("base", "")        # подложка; пусто — по теме

        self.db = TowerDB()
        self._net_init()
        self.building_data = BuildingData(cache_dir=BASE_DIR / "buildings_cache")
        self.towers = []
        self.selected_id = None
        self.tower_counter = 1
        self.tower_types = dict(DEFAULT_PRESETS)
        self.zone_algo = None
        self.zone_forbidden = []
        self._mode = "navigate"
        self._map_ready = False
        self._job_queue = []
        self._worker = self._fetch_worker = self._cache_worker = self._algo_worker = None
        # Занятость отслеживаем флагами, а не isRunning(): сигнал «готово» приходит, когда поток
        # ещё не завершился, и проверка isRunning() в обработчике «теряла» очередь задач.
        self._calc_busy = self._fetch_busy = False
        self._threads = set()                  # ссылки на потоки до их завершения (иначе Qt падает)
        self._fetch_queue = []                 # точки (lat, lon), где нужны здания для вышек
        self._hover_point = None               # последняя точка под курсором для предзагрузки
        self._import_ids = {}                  # id вышки в импортируемом проекте -> новый id в БД
        self._js_revs = {}                     # id вышки -> версия расчёта, уже переданная карте
        self._last_hover_time = 0.0
        self._block_table = False
        self._probe_point = None
        self._probe_results = []
        self._measure_points = []
        self._icon_actions = []
        self._mode_actions = {}
        self._undo_stack = []
        self._is_undoing = False
        self._clipboard_towers = []
        self._clipboard_devices = []
        self._clipboard_center = None
        self.selected_ids = []
        self.coverage_mode = "standard"

        self.init_ui()
        self.apply_theme(self.theme_name)
        self.load_state_from_db()
        self._start_cache_loading()

    # ------------------------------------------------------------------ #
    #  Построение интерфейса
    # ------------------------------------------------------------------ #
    @property
    def th(self):
        return theming.THEMES[self.theme_name]

    def push_undo(self, undo_fn, desc="Действие"):
        """Поместить действие в стек отмены (Ctrl+Z / Cmd+Z)."""
        if getattr(self, "_is_undoing", False):
            return
        self._undo_stack.append({"desc": desc, "undo": undo_fn})
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._update_undo_state()

    def undo(self):
        """Отменить последнее действие (Ctrl+Z / Cmd+Z)."""
        if not self._undo_stack:
            self.statusBar().showMessage("Нечего отменять.")
            return
        item = self._undo_stack.pop()
        self._is_undoing = True
        try:
            item["undo"]()
            self.statusBar().showMessage(f"Отменено: {item['desc']}")
        except Exception as e:
            self.statusBar().showMessage(f"Ошибка отмены: {e}")
        finally:
            self._is_undoing = False
            self._update_undo_state()

    def _update_undo_state(self):
        if hasattr(self, "act_undo"):
            if self._undo_stack:
                last = self._undo_stack[-1]["desc"]
                self.act_undo.setEnabled(True)
                self.act_undo.setToolTip(f"<b>Отменить (Ctrl+Z)</b><br>{last}")
            else:
                self.act_undo.setEnabled(False)
                self.act_undo.setToolTip("<b>Отменить (Ctrl+Z)</b><br>Нет действий для отмены")

    def toggle_coverage_mode(self, checked):
        """Переключение режима отображения зон: обычный или лучшая сила (штриховка)."""
        self.coverage_mode = "best_server" if checked else "standard"
        self._js(f"api.setCoverageMode('{self.coverage_mode}');")
        msg = ("Режим зон: лучшая сила сигнала (штриховка)" if checked
               else "Режим зон: стандартный (наложение)")
        self.statusBar().showMessage(msg)

    def trigger_radial_menu(self):
        """Открытие кругового меню выбора оборудования (Ctrl+A)."""
        self._js("api.openRadialMenu();")

    def copy_selected(self):
        """Копирование выбранных вышек в буфер обмена (Ctrl+C)."""
        towers_to_copy = []
        if self.selected_ids:
            towers_to_copy = [t for t in self.towers if t.db_id in self.selected_ids]
        if not towers_to_copy:
            cur = self._current_tower()
            if cur:
                towers_to_copy = [cur]
        if not towers_to_copy:
            self.statusBar().showMessage("Нет выбранных вышек для копирования (выделите рамкой Shift+ЛКМ или кликом).")
            return
        self._clipboard_towers = [t.to_dict() for t in towers_to_copy]
        c_lat = sum(t["lat"] for t in self._clipboard_towers) / len(self._clipboard_towers)
        c_lon = sum(t["lon"] for t in self._clipboard_towers) / len(self._clipboard_towers)
        self._clipboard_center = (c_lat, c_lon)
        self.statusBar().showMessage(f"Скопировано в буфер: вышек — {len(self._clipboard_towers)} (Ctrl+V для вставки).")

    def paste_from_clipboard(self):
        """Вставка вышек из буфера обмена (Ctrl+V) со сдвигом или в точку курсора."""
        if not self._clipboard_towers or not self._clipboard_center:
            self.statusBar().showMessage("Буфер обмена пуст. Сначала скопируйте вышку (Ctrl+C).")
            return
        target_lat, target_lon = None, None
        if self._hover_point:
            target_lat, target_lon = self._hover_point
        else:
            target_lat = self._clipboard_center[0] - 0.0004
            target_lon = self._clipboard_center[1] + 0.0006
        c_lat, c_lon = self._clipboard_center
        jobs = []
        new_names = []
        for t_data in self._clipboard_towers:
            dlat = t_data["lat"] - c_lat
            dlon = t_data["lon"] - c_lon
            p_lat = target_lat + dlat
            p_lon = target_lon + dlon
            name = self._next_name()
            new_names.append(name)
            job = self._job_from_type(name, p_lat, p_lon, t_data.get("type_name"), t_data)
            job["params"] = {k: t_data[k] for k in PARAM_KEYS}
            job["mount"] = t_data.get("mount")
            jobs.append(job)
        if jobs:
            self._enqueue(jobs)
            def undo_paste():
                for n in new_names:
                    tw = next((x for x in self.towers if x.name == n), None)
                    if tw:
                        self.db.delete_tower(tw.db_id)
                        self.towers = [x for x in self.towers if x.db_id != tw.db_id]
                self._refresh_all()
            self.push_undo(undo_paste, f"Вставка {len(jobs)} вышек")
            self.statusBar().showMessage(f"Вставлено вышек: {len(jobs)}.")

    def _act(self, text, icon=None, slot=None, checkable=False, shortcut=None, tip=None):
        act = QAction(text, self)
        if checkable:
            act.setCheckable(True)
        if slot:
            (act.toggled if checkable else act.triggered).connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if tip:
            act.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
        if icon:
            self._icon_actions.append((act, icon))
        return act

    def init_ui(self):
        self._build_actions()
        self._build_menu()
        self._build_toolbar()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())

        self.web_view = QWebEngineView()
        self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)   # ПКМ на карте = «отменить точку»
        self.web_view.setPage(MapPage(self, self.web_view.page().profile(), self.web_view))
        self.web_view.loadFinished.connect(self._on_map_loaded)
        splitter.addWidget(self.web_view)
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([280, 800, 420])
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

        sb = self.statusBar()
        self.lbl_buildings = QLabel("Здания: нет данных")
        self.lbl_towers = QLabel("Вышек: 0")
        self.lbl_mode = QLabel("")
        self.lbl_net = QLabel("Сеть: устройств 0, линков 0")
        for w in (self.lbl_buildings, self.lbl_towers, self.lbl_net, self.lbl_mode):
            sb.addPermanentWidget(w)
        sb.showMessage("Готово")

        QShortcut(QKeySequence("Esc"), self, activated=lambda: self._mode_actions["navigate"].setChecked(True))
        self._mode_actions["navigate"].setChecked(True)      # только теперь: set_mode трогает виджеты интерфейса
        self._update_action_states()
        self.web_view.setHtml(load_map_html(), QUrl("http://localhost"))

    def _build_actions(self):
        grp = QActionGroup(self)
        grp.setExclusive(True)
        defs = [
            ("navigate", "Навигация", "navigate", "Ctrl+1",
             "<b>Навигация (Ctrl+1)</b><br>"
             "Перемещение и панорамирование карты.<br>"
             "• <i>Клик:</i> выделить вышку или устройство<br>"
             "• <i>Зажать ЛКМ:</i> перетащить вышку с расчётом на лету<br>"
             "• <i>Синяя ручка:</i> вращение азимута сектора"),
            ("place", "Поставить вышку", "tower", "Ctrl+2",
             "<b>Поставить вышку (Ctrl+2)</b><br>"
             "Размещение антенны выбранного типа на местности.<br>"
             "• <i>Клик на здание:</i> монтаж на крышу (высота крыши + 3 м)<br>"
             "• <i>Клик на землю:</i> стандартная опора (30 м)<br>"
             "• <i>Клик у каркаса:</i> крепление к конструкции"),
            ("measure", "Линейка", "ruler", "Ctrl+3",
             "<b>Линейка (Ctrl+3)</b><br>"
             "Измерение расстояний и азимутов между точками.<br>"
             "• <i>ЛКМ:</i> добавить точку трассы<br>"
             "• <i>ПКМ:</i> отменить последнюю точку"),
            ("probe", "Замер сигнала", "probe", "Ctrl+4",
             "<b>Замер сигнала (Ctrl+4)</b><br>"
             "Анализ мощности принимаемого сигнала (дБм), доминирующих вышек "
             "и потерь на препятствиях в выбранной точке карты."),
            ("zone_algo", "Участок алгоритма", "polygon", None,
             "<b>Участок алгоритма</b><br>"
             "Рисование полигона для автоматической расстановки вышек.<br>"
             "• <i>ЛКМ:</i> вершины полигона<br>"
             "• <i>Двойной клик / Enter:</i> завершить контур"),
            ("zone_forbidden", "Запретная зона", "ban", None,
             "<b>Запретная зона</b><br>"
             "Рисование зоны, где запрещено размещать базовые станции."),
            ("device", "Устройство", "server", "Ctrl+5",
             "<b>Сетевое устройство (Ctrl+5)</b><br>"
             "Установка сетевого оборудования (CORE, L2/L3 коммутаторы, маршрутизаторы).<br>"
             "Тип выбирается стрелкой рядом или во вкладке «Сеть»."),
            ("cable", "Кабель", "link", "Ctrl+6",
             "<b>Прокладка кабеля (Ctrl+6)</b><br>"
             "Соединение двух устройств или вышки кабелем.<br>"
             "• <i>1-й клик:</i> выбрать начальный узел<br>"
             "• <i>2-й клик:</i> выбрать целевой узел<br>"
             "• <i>ПКМ / Esc:</i> отменить")
        ]
        for name, text, icon, sc, tip in defs:
            act = self._act(text, icon, checkable=True, shortcut=sc, tip=tip)
            act.toggled.connect(lambda checked, n=name: checked and self.set_mode(n))
            grp.addAction(act)
            self._mode_actions[name] = act

        self.act_edit = self._act("Редактировать", "edit", self.edit_selected_tower,
                                  tip="<b>Редактировать вышку</b><br>Изменить название, параметры излучения и диаграмму направленности.")
        self.act_delete = self._act("Удалить", "trash", self.delete_selected_tower, shortcut="Del",
                                    tip="<b>Удалить вышку (Del)</b><br>Удалить выбранную базовую станцию и её кабели.")
        self.act_undo = self._act("Отменить", "undo", self.undo, shortcut=QKeySequence.StandardKey.Undo,
                                  tip="<b>Отменить (Ctrl+Z / Cmd+Z)</b><br>Отмена последнего действия: размещение, перемещение, удаление вышки, поворот азимута, параметры.")
        self.act_undo.setEnabled(False)
        self.act_copy = self._act("Копировать", "copy", self.copy_selected, shortcut=QKeySequence.StandardKey.Copy,
                                  tip="<b>Копировать (Ctrl+C / Cmd+C)</b><br>Копировать выбранные вышки в буфер обмена.")
        self.act_paste = self._act("Вставить", "clipboard", self.paste_from_clipboard, shortcut=QKeySequence.StandardKey.Paste,
                                   tip="<b>Вставить (Ctrl+V / Cmd+V)</b><br>Вставить вышки из буфера обмена в точку курсора.")
        self.act_best_server = self._act("Штриховка (лучший сигнал)", "hatch", self.toggle_coverage_mode, checkable=True,
                                         tip="<b>Штриховка покрытия (лучший сигнал)</b><br>Отображение зон со штриховкой: наложение вышек показывает лучшую силу сигнала.")
        self.act_radial = self._act("Круговое меню", "radial", self.trigger_radial_menu, shortcut="Ctrl+A",
                                    tip="<b>Круговое меню (Ctrl+A)</b><br>Быстрый круговой выбор всех типов вышек, опор и сетевых устройств.")
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self.copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self.paste_from_clipboard)
        QShortcut(QKeySequence("Ctrl+A"), self, activated=self.trigger_radial_menu)
        self.act_recalc = self._act("Пересчитать всё", "refresh", self.recalc_all, shortcut="F5",
                                    tip="<b>Пересчитать всё (F5)</b><br>Полный пересчёт зон покрытия всех вышек по зданиям.")
        self.act_antenna_catalog = self._act("Каталог антенн", "layers", self.open_antenna_catalog_dialog,
                                             tip="<b>Каталог антенн</b><br>База данных серийных антенн (Huawei, Kathrein, Ericsson, CommScope...).<br>Выбор усиления, частот и мощности.")
        self.act_create_structure = self._act("Каркас вышки", "structure", self.open_create_structure_dialog,
                                              tip="<b>Каркас вышки</b><br>Создание опорной конструкции (решётчатой башни, мачты, столба).<br>Позволяет размещать несколько секторов на одной опоре.")
        self.act_net_auto = self._act("Авто-IP", "sliders", self.net_autoconfigure,
                                      tip="<b>Авто-IP</b><br>Автоматическое назначение IP-адресов подсетей (/30, /24) и построение таблиц маршрутизации.")
        self.act_bom = self._act("Спецификация", "list", self.net_export_bom,
                                 tip="<b>Спецификация оборудования (BOM)</b><br>Формирование сметы оборудования, трансиверов и длин кабелей (CSV).")
        self.act_snmp = self._act("SNMP", "activity", self.snmp_selected, shortcut="Ctrl+8",
                                  tip="<b>SNMP-опрос (Ctrl+8)</b><br>Опрос параметров агента SNMP базовой станции или коммутатора.")
        self.act_load = self._act("Тест нагрузки", "zap", self.run_load_test, shortcut="Ctrl+L",
                                  tip="<b>Тест нагрузки (Ctrl+L)</b><br>Моделирование абонентов, загрузка радиоканалов и анимация движения пакетов.")
        self.act_load_clear = self._act("Сброс", "stop", self.clear_load_results,
                                        tip="<b>Сброс теста</b><br>Очистить карту от абонентов и линий нагрузки.")
        self.act_goto = self._act("Координаты…", "goto", self.goto_coords, shortcut="Ctrl+G",
                                  tip="<b>Координаты (Ctrl+G)</b><br>Быстрый центрированный переход к точке (широта, долгота).")
        self.act_calc = self._act("Калькулятор", "calc", self.show_calculator, shortcut="Ctrl+K",
                                  tip="<b>Радиокалькулятор (Ctrl+K)</b><br>Бюджет линии связи, зона Френеля и тепловой шум.")
        self.act_theme = self._act("Сменить тему", "moon", self.toggle_theme, shortcut="Ctrl+T",
                                   tip="<b>Сменить тему (Ctrl+T)</b><br>Переключение тёмного и светлого оформления.")
        self.act_import = self._act("Импорт проекта…", "upload", self.import_project,
                                    tip="<b>Импорт проекта</b><br>Загрузка сохранённой радиосети из JSON.")
        self.act_exp_json = self._act("Проект (JSON)…", None, self.export_project)
        self.act_exp_csv = self._act("Таблица вышек (CSV)…", None, self.export_csv)
        self.act_exp_geo = self._act("Покрытие (GeoJSON для QGIS)…", None, self.export_geojson)
        self.act_exp_png = self._act("Снимок карты (PNG)…", None, self.export_png)
        self.act_clear_map = self._act("Очистить карту (БД не трогать)", None, self.clear_map_only)
        self.act_clear_db = self._act("Очистить БД…", None, self.clear_all)
        self.act_quit = self._act("Выход", None, self.close, shortcut="Ctrl+Q")
        self.act_help = self._act("Горячие клавиши", None, self.show_help)
        self.act_about = self._act("О программе", None, self.show_about)
        self.base_actions = {}
        bgrp = QActionGroup(self)
        for key, text in (("", "Как тема"), ("dark", "Тёмная"), ("light", "Светлая"), ("detailed", "Подробная (Liberty)")):
            a = self._act(text, None, checkable=True)
            a.setChecked(key == self.base_name)
            a.toggled.connect(lambda checked, k=key: checked and self.set_base(k))
            bgrp.addAction(a)
            self.base_actions[key] = a

    def _build_menu(self):
        mb = self.menuBar()
        m = mb.addMenu("Файл")
        m.addAction(self.act_import)
        exp = m.addMenu("Экспорт")
        for a in (self.act_exp_json, self.act_exp_csv, self.act_exp_geo, self.act_exp_png):
            exp.addAction(a)
        m.addSeparator()
        m.addAction(self.act_clear_map)
        m.addAction(self.act_clear_db)
        m.addSeparator()
        m.addAction(self.act_quit)
        m_edit = mb.addMenu("Правка")
        m_edit.addAction(self.act_undo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_copy)
        m_edit.addAction(self.act_paste)
        m_edit.addAction(self.act_radial)
        m_edit.addSeparator()
        m_edit.addAction(self.act_edit)
        m_edit.addAction(self.act_delete)
        m = mb.addMenu("Инструменты")
        for name in ("navigate", "place", "measure", "probe"):
            m.addAction(self._mode_actions[name])
        m.addSeparator()
        for a in (self.act_recalc, self.act_goto, self.act_calc):
            m.addAction(a)
        m = mb.addMenu("Вид")
        m.addAction(self.act_theme)
        m.addAction(self.act_best_server)
        bm = m.addMenu("Подложка карты")
        for a in self.base_actions.values():
            bm.addAction(a)
        m = mb.addMenu("Справка")
        m.addAction(self.act_help)
        m.addAction(self.act_about)

    def _build_toolbar(self):
        tb = QToolBar("Инструменты")
        tb.setMovable(False)
        self.addToolBar(tb)
        bar = RibbonBar()
        m = self._mode_actions
        g = bar.add_group("Карта")
        g.add_action(m["navigate"])
        g.add_action(self.act_undo)
        g.add_action(self.act_copy)
        g.add_action(self.act_paste)
        g.add_action(self.act_goto)
        g = bar.add_group("Радиосеть")
        g.add_action(m["place"])
        g.add_action(self.act_radial)
        g.add_action(self.act_best_server)
        g.add_action(self.act_create_structure)
        g.add_action(self.act_antenna_catalog)
        g.add_action(self.act_edit)
        g.add_action(self.act_delete)
        g.add_action(self.act_recalc)
        g = bar.add_group("Транспортная сеть")
        self._device_menu = self.build_device_menu()
        self._cable_menu = self.build_cable_menu()
        g.add_action(m["device"], self._device_menu)
        g.add_action(m["cable"], self._cable_menu)
        g.add_action(self.act_net_auto)
        g.add_action(self.act_snmp)
        g.add_action(self.act_bom)
        g = bar.add_group("Измерения")
        g.add_action(m["measure"])
        g.add_action(m["probe"])
        g.add_action(self.act_calc)
        g = bar.add_group("Нагрузка")
        g.add_action(self.act_load)
        g.add_action(self.act_load_clear)
        bar.add_stretch()
        g = bar.add_group("Файл и вид")
        g.add_action(self.act_import)
        exp_btn = QToolButton()
        exp_btn.setToolTip("<h3>Экспорт проекта и данных</h3>• Проект в JSON<br>• Таблица вышек в CSV<br>• Покрытие в GeoJSON (QGIS)<br>• Снимок карты PNG")
        exp_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        exp_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        exp_btn.setIconSize(QSize(24, 24))
        exp_btn.setProperty("ribbon", True)
        exp_btn.setAutoRaise(True)
        menu = QMenu(exp_btn)
        for a_ in (self.act_exp_json, self.act_exp_csv, self.act_exp_geo, self.act_exp_png):
            menu.addAction(a_)
        exp_btn.setMenu(menu)
        self._export_button = exp_btn
        g.add_widget(exp_btn)
        g.add_action(self.act_theme)
        tb.addWidget(bar)

    def _section(self, text):
        lbl = QLabel(text.upper())
        lbl.setProperty("role", "section")
        return lbl

    def _build_left_panel(self):
        tabs = QTabWidget()
        tabs.setMinimumWidth(270)
        tabs.setMaximumWidth(360)

        # --- Радио ---
        radio = QWidget()
        radio.setObjectName("panel")
        lay = QVBoxLayout(radio)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(8)
        lay.addWidget(self._section("Тип вышки"))
        self.active_type_combo = QComboBox()
        self.active_type_combo.addItems(list(self.tower_types.keys()))
        self.active_type_combo.currentTextChanged.connect(self._update_type_summary)
        lay.addWidget(self.active_type_combo)
        self.type_summary = QLabel()
        self.type_summary.setProperty("role", "dim")
        self.type_summary.setWordWrap(True)
        lay.addWidget(self.type_summary)

        # Кнопки управления типами: Каталог (полная ширина), ниже Новый тип и Удалить
        btn_catalog = QPushButton("📖 Каталог антенн…")
        btn_catalog.setToolTip("Выбрать промышленную модель антенны из базы мировых производителей")
        btn_catalog.clicked.connect(self.open_antenna_catalog_dialog)
        lay.addWidget(btn_catalog)

        h_types_btn = QHBoxLayout()
        h_types_btn.setSpacing(6)
        btn_new = QPushButton("+ Новый тип…")
        btn_new.setToolTip("Создать собственный профиль вышки с нуля")
        btn_new.clicked.connect(self.show_add_tower_dialog)
        h_types_btn.addWidget(btn_new)

        self.btn_del_type = QPushButton("Удалить тип")
        self.btn_del_type.setToolTip("Удалить выбранный пользовательский тип вышки")
        self.btn_del_type.clicked.connect(self.delete_current_tower_type)
        h_types_btn.addWidget(self.btn_del_type)

        lay.addLayout(h_types_btn)
        self.chk_buildings = QCheckBox("Учитывать затухание в зданиях")
        self.chk_buildings.setChecked(True)
        self.chk_buildings.toggled.connect(lambda _: self.recalc_all())
        lay.addWidget(self.chk_buildings)
        self.chk_fresnel = QCheckBox("Дифракция в зоне Френеля (дома ниже антенны)")
        self.chk_fresnel.setToolTip("Здания ниже антенны, попавшие в 1-ю зону Френеля трассы «вышка → абонент», "
                                    "дают потери дифракции (ITU-R P.526, доминирующее препятствие)")
        self.chk_fresnel.setChecked(self._setting_bool("model_fresnel", True))
        self.chk_fresnel.toggled.connect(lambda v: self._model_option_changed("model_fresnel", v))
        lay.addWidget(self.chk_fresnel)
        self.chk_pattern = QCheckBox(f"Боковые и задний лепестки антенны (−{FRONT_TO_BACK_DB:.0f} дБ)")
        self.chk_pattern.setToolTip("Диаграмма секторной антенны по 3GPP TR 36.814: плавный спад луча и "
                                    "излучение назад/вбок, а не «ноль» вне сектора")
        self.chk_pattern.setChecked(self._setting_bool("model_pattern", True))
        self.chk_pattern.toggled.connect(lambda v: self._model_option_changed("model_pattern", v))
        lay.addWidget(self.chk_pattern)
        lay.addStretch()
        tabs.addTab(radio, "Радио")

        # --- Сеть ---
        tabs.addTab(self.build_network_palette(), "Сеть")

        # --- Участок и алгоритмы ---
        zones = QWidget()
        zones.setObjectName("panel")
        lay = QVBoxLayout(zones)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(8)
        lay.addWidget(self._section("Участок и зоны"))
        for name in ("zone_algo", "zone_forbidden"):
            btn = QToolButton()
            btn.setDefaultAction(self._mode_actions[name])
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setProperty("role", "zone")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lay.addWidget(btn)
        self.chk_rect = QCheckBox("Прямоугольник (2 клика)")
        self.chk_rect.toggled.connect(lambda v: self._js(f"api.setRectShape({str(bool(v)).lower()});"))
        lay.addWidget(self.chk_rect)
        btn_clear_zones = QPushButton("Очистить зоны")
        btn_clear_zones.clicked.connect(self.clear_zones)
        lay.addWidget(btn_clear_zones)
        self.zone_info = QLabel("Участок не задан")
        self.zone_info.setProperty("role", "dim")
        self.zone_info.setWordWrap(True)
        lay.addWidget(self.zone_info)
        lay.addWidget(self._section("Автоматическая расстановка"))
        self.algo_combo = QComboBox()
        self.algo_combo.addItems(list(auto_placement.ALGORITHMS.keys()))
        lay.addWidget(self.algo_combo)
        row = QLabel("Максимум вышек:")
        row.setProperty("role", "dim")
        lay.addWidget(row)
        self.max_towers_spin = QSpinBox()
        self.max_towers_spin.setRange(1, 500)
        self.max_towers_spin.setValue(10)
        lay.addWidget(self.max_towers_spin)
        self.btn_run_algo = QPushButton("Запустить алгоритм")
        self.btn_run_algo.setProperty("primary", True)
        self.btn_run_algo.clicked.connect(self.run_algorithm)
        lay.addWidget(self.btn_run_algo)
        lay.addWidget(self._section("Покрытие участка"))
        self.stats_label = QLabel("—")
        self.stats_label.setTextFormat(Qt.TextFormat.RichText)
        self.stats_label.setWordWrap(True)
        lay.addWidget(self.stats_label)
        lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(zones)
        tabs.addTab(scroll, "Участок")
        self.left_tabs = tabs
        self._update_type_summary()
        return tabs

    def _build_right_panel(self):
        panel = QWidget()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        lay.addWidget(self._section("Вышки"))

        split = QSplitter(Qt.Orientation.Vertical)
        self.tower_table = QTableWidget(0, 4)
        self.tower_table.setHorizontalHeaderLabels(["Имя", "Тип", "МГц", "R слаб., км"])
        self.tower_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tower_table.verticalHeader().setVisible(False)
        self.tower_table.setAlternatingRowColors(True)
        self.tower_table.setShowGrid(False)
        self.tower_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tower_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tower_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tower_table.itemSelectionChanged.connect(self._on_table_selection)
        split.addWidget(self.tower_table)

        tabs = QTabWidget()
        tower_page = QWidget()
        tl = QVBoxLayout(tower_page)
        tl.setContentsMargins(0, 4, 0, 0)
        tl.setSpacing(4)

        # Редактор характеристик вышки прямо на правой панели
        scroll_ed = QScrollArea()
        scroll_ed.setWidgetResizable(True)
        scroll_ed.setFrameShape(QScrollArea.Shape.NoFrame)
        ed_widget = QWidget()
        el = QVBoxLayout(ed_widget)
        el.setContentsMargins(0, 0, 4, 0)
        el.setSpacing(4)

        f_prop = QFormLayout()
        f_prop.setSpacing(3)
        self.ed_tower_name = QLineEdit()
        self.ed_tower_name.setPlaceholderText("Имя вышки")
        f_prop.addRow("Имя:", self.ed_tower_name)

        self.ed_tower_type = QComboBox()
        self.ed_tower_type.currentIndexChanged.connect(self._on_tower_panel_type_changed)
        f_prop.addRow("Тип / профиль:", self.ed_tower_type)

        self.ed_tower_coords = QLineEdit()
        self.ed_tower_coords.setPlaceholderText("lat, lon")
        f_prop.addRow("Координаты:", self.ed_tower_coords)

        # Статус монтажа и кнопки оснащения антенны
        h_mount_btn = QHBoxLayout()
        h_mount_btn.setSpacing(6)
        self.lbl_mount_status = QLabel("—")
        self.lbl_mount_status.setStyleSheet("color: #3e8bff; font-weight: 600; font-size: 11px;")
        self.btn_tower_mount = QPushButton("🏢/🗼 Монтаж…")
        self.btn_tower_mount.setToolTip("Прикрепить антенну к зданию или каркасу вышки")
        self.btn_tower_mount.clicked.connect(self.open_mount_manager_dialog)
        h_mount_btn.addWidget(self.lbl_mount_status, stretch=1)
        h_mount_btn.addWidget(self.btn_tower_mount)
        f_prop.addRow("Крепление:", h_mount_btn)

        # Выпадающий список моделей из каталога антенн
        h_cat = QHBoxLayout()
        h_cat.setSpacing(4)
        self.cmb_tower_antenna_model = QComboBox()
        self.cmb_tower_antenna_model.setToolTip("Выбрать реальную заводскую модель антенны из каталога (Huawei, Kathrein, Ericsson, CommScope...)")
        self.cmb_tower_antenna_model.currentIndexChanged.connect(self._on_antenna_model_selected)
        btn_open_catalog = QPushButton("🔍")
        btn_open_catalog.setToolTip("Открыть полный каталог антенн с фильтрами и характеристиками")
        btn_open_catalog.setFixedWidth(30)
        btn_open_catalog.clicked.connect(self.open_antenna_catalog_dialog)
        h_cat.addWidget(self.cmb_tower_antenna_model, stretch=1)
        h_cat.addWidget(btn_open_catalog)
        f_prop.addRow("Каталог вышек:", h_cat)
        self._populate_antenna_dropdown()

        self.ed_tower_height = QDoubleSpinBox()
        self.ed_tower_height.setRange(0.0, 300.0)
        self.ed_tower_height.setSuffix(" м")
        f_prop.addRow("Высота:", self.ed_tower_height)

        self.ed_tower_freq = QDoubleSpinBox()
        self.ed_tower_freq.setRange(100.0, 60000.0)
        self.ed_tower_freq.setSuffix(" МГц")
        f_prop.addRow("Частота:", self.ed_tower_freq)

        self.ed_tower_power = QDoubleSpinBox()
        self.ed_tower_power.setRange(0.1, 500.0)
        self.ed_tower_power.setSuffix(" Вт")
        self.ed_tower_power.valueChanged.connect(self._update_tower_panel_eirp)
        f_prop.addRow("Мощность:", self.ed_tower_power)

        self.lbl_tower_eirp = QLabel("—")
        self.lbl_tower_eirp.setProperty("role", "dim")
        f_prop.addRow("EIRP:", self.lbl_tower_eirp)

        self.ed_tower_tilt = QDoubleSpinBox()
        self.ed_tower_tilt.setRange(-30.0, 90.0)
        self.ed_tower_tilt.setSuffix(" °")
        f_prop.addRow("Наклон:", self.ed_tower_tilt)

        self.ed_tower_wall_loss = QDoubleSpinBox()
        self.ed_tower_wall_loss.setRange(0.0, 60.0)
        self.ed_tower_wall_loss.setSuffix(" дБ")
        f_prop.addRow("Потери стены:", self.ed_tower_wall_loss)

        self.ed_tower_azimuth = QSpinBox()
        self.ed_tower_azimuth.setRange(0, 359)
        self.ed_tower_azimuth.setSuffix(" °")
        f_prop.addRow("Азимут:", self.ed_tower_azimuth)

        self.ed_tower_beamwidth = QSpinBox()
        self.ed_tower_beamwidth.setRange(1, 360)
        self.ed_tower_beamwidth.setSuffix(" °")
        f_prop.addRow("Ширина луча:", self.ed_tower_beamwidth)
        el.addLayout(f_prop)

        row_btn = QHBoxLayout()
        self.btn_tower_apply = QPushButton("💾 Применить")
        self.btn_tower_apply.setToolTip("Сохранить изменения и пересчитать зоны покрытия вышки")
        self.btn_tower_apply.clicked.connect(self._apply_tower_panel_edits)
        self.btn_tower_del_panel = QPushButton("Удалить")
        self.btn_tower_del_panel.clicked.connect(self.delete_selected_tower)
        row_btn.addWidget(self.btn_tower_apply, stretch=2)
        row_btn.addWidget(self.btn_tower_del_panel, stretch=1)
        el.addLayout(row_btn)

        self.tower_info = QTextBrowser()
        self.tower_info.setPlaceholderText("Выберите вышку на карте или в таблице")
        self.tower_info.setMaximumHeight(140)
        el.addWidget(self.tower_info)

        scroll_ed.setWidget(ed_widget)
        tl.addWidget(scroll_ed)

        self.btn_tower_snmp = QPushButton("SNMP-опрос вышки…")
        self.btn_tower_snmp.setToolTip("Таблицы агента SNMP базовой станции: интерфейсы, IP, соседи, радио")
        self.btn_tower_snmp.clicked.connect(self.snmp_selected)
        tl.addWidget(self.btn_tower_snmp)
        tabs.addTab(tower_page, "Вышка")

        sig_page = QWidget()
        sl = QVBoxLayout(sig_page)
        sl.setContentsMargins(0, 6, 0, 0)
        self.signal_info = QTextBrowser()
        self.signal_info.setPlaceholderText("Включите «Замер сигнала» и кликните в любую точку карты")
        sl.addWidget(self.signal_info)
        self.btn_profile = QPushButton("Профиль трассы…")
        self.btn_profile.setEnabled(False)
        self.btn_profile.clicked.connect(self.show_link_profile)
        sl.addWidget(self.btn_profile)
        tabs.addTab(sig_page, "Сигнал")

        meas_page = QWidget()
        ml = QVBoxLayout(meas_page)
        ml.setContentsMargins(0, 6, 0, 0)
        self.measure_info = QTextBrowser()
        self.measure_info.setPlaceholderText("Включите «Линейку» и кликайте по карте")
        ml.addWidget(self.measure_info)
        btn_clear = QPushButton("Очистить измерения")
        btn_clear.clicked.connect(lambda: self._js("api.clearMeasure();"))
        ml.addWidget(btn_clear)
        tabs.addTab(meas_page, "Измерения")
        self.net_page = self.build_network_tab()
        tabs.addTab(self.net_page, "Сеть")
        self.load_page = self.build_load_tab()
        tabs.addTab(self.load_page, "Нагрузка")
        self.tabs = tabs
        split.addWidget(tabs)
        split.setSizes([220, 560])
        lay.addWidget(split, stretch=1)
        panel.setMinimumWidth(390)
        panel.setMaximumWidth(620)
        return panel

    # ------------------------------------------------------------------ #
    #  Тема
    # ------------------------------------------------------------------ #
    def apply_theme(self, name):
        self.theme_name = name
        self.settings.setValue("theme", name)
        QApplication.instance().setStyleSheet(theming.build_qss(name))
        t = self.th
        for act, icon in self._icon_actions:
            if act is self.act_theme:
                icon = "sun" if name == "dark" else "moon"
            act.setIcon(theming.make_icon(icon, t["text"], t["accent_text"]))
        self._export_button.setIcon(theming.make_icon("download", t["text"]))
        self._push_theme()
        self._update_type_summary()
        if hasattr(self, "ld_chart"):
            self.ld_chart.theme = t
            self.ld_chart.update()
        self._render_info_panels()
        self._render_net_panels()

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def set_base(self, key):
        self.base_name = key
        self.settings.setValue("base", key)
        self._push_theme(force_base=True)

    def _push_theme(self, force_base=False):
        if not self._map_ready:
            return
        self._js_revs = {}
        self._js(f"api.setTheme('{self.theme_name}');")
        self._js(f"api.setBase('{self.base_name or self.theme_name}');")     # JS пропускает повтор той же подложки
        self._push_towers()
        self._push_net()
        self._push_zones()
        if getattr(self, "coverage_mode", "standard") != "standard":
            self._js(f"api.setCoverageMode('{self.coverage_mode}');")

    # ------------------------------------------------------------------ #
    #  Связь с картой
    # ------------------------------------------------------------------ #
    def _js(self, code):
        if self._map_ready:
            self.web_view.page().runJavaScript(code)

    def _on_map_loaded(self, ok):
        self._map_ready = bool(ok)
        self._js_revs = {}                         # страница новая — лучи нужно передать заново
        if not ok:
            self.statusBar().showMessage("Не удалось загрузить страницу карты")
            return
        self._push_theme()
        self._push_towers()
        self._push_zones()
        self._push_net()
        self._js(f"api.setMapMode('{self._mode}');")
        self._js(f"api.setRectShape({str(self.chk_rect.isChecked()).lower()});")

    def _push_towers(self):
        """Список вышек на карту. Лучи (основной объём данных) — только у вышек с новой версией расчёта."""
        if not self._map_ready:
            return
        out, revs = [], {}
        for t in self.towers:
            d = t.to_js()
            if self._js_revs.get(t.db_id) == t.rev:
                d = dict(d, rays=None)                 # карта возьмёт лучи из своего кэша
            revs[t.db_id] = t.rev
            out.append(d)
        self._js_revs = revs
        self._js(f"api.setTowers({json.dumps(out)});")
        self._js(f"api.selectTower({json.dumps(self.selected_id)});")

    def _push_zones(self):
        self._js(f"api.setZones({json.dumps({'algo': self.zone_algo, 'forbidden': self.zone_forbidden})});")

    def on_map_event(self, ev):
        try:
            self._handle_map_event(ev)
        except Exception:                      # исключение в обработчике Qt = аварийное завершение, поэтому ловим
            report_exception(*sys.exc_info())

    def _handle_map_event(self, ev):
        t = ev.get("type")
        if t == "place":
            self.place_tower_at_coords(ev["lat"], ev["lon"],
                                       pitch=ev.get("pitch", 0.0),
                                       bearing=ev.get("bearing", 0.0),
                                       bld_height=ev.get("bld_height", 0.0))
        elif t == "selection_box":
            t_ids = ev.get("tower_ids", [])
            self.selected_ids = t_ids
            if len(t_ids) == 1:
                self.select_tower_by_id(t_ids[0])
            elif len(t_ids) > 1:
                self.selected_id = t_ids[0]
                self._update_action_states()
                self.statusBar().showMessage(f"Выделено рамкой: вышек — {len(t_ids)} (Ctrl+C для копирования)")
        elif t == "style_loaded":
            self._js_revs = {}
            self._push_towers()
            self._push_net()
            self._push_zones()
            if getattr(self, "coverage_mode", "standard") != "standard":
                self._js(f"api.setCoverageMode('{self.coverage_mode}');")
        elif t == "radial_select":
            item = ev.get("item", {})
            lat = ev.get("lat")
            lon = ev.get("lon")
            itype = item.get("type")
            if itype == "tower":
                preset = item.get("preset")
                if preset in self.tower_types:
                    self.active_type_combo.setCurrentText(preset)
                self.set_mode("place")
                if lat and lon:
                    self.place_tower_at_coords(lat, lon)
            elif itype == "device":
                model_id = item.get("model")
                if hasattr(self, "net_set_model"):
                    self.net_set_model(model_id)
                self.set_mode("device")
                if lat and lon:
                    self.net_place_device(lat, lon)
            elif itype == "structure":
                try:
                    import structures
                    s = structures.create_structure(
                        self.db.conn,
                        name=item.get("name", "Каркас"),
                        kind=item.get("kind", "lattice"),
                        lat=lat or 47.0101,
                        lon=lon or 28.8638,
                        height=float(item.get("height", 30.0))
                    )
                    self.structures[s.id] = s
                    self._push_towers()
                    self.statusBar().showMessage(f"Создан каркас «{s.name}» (высота {s.height:.0f} м).")
                except Exception as e:
                    self.statusBar().showMessage(f"Ошибка создания каркаса: {e}")
        elif t == "select":
            self.select_tower_by_id(ev.get("id"))
        elif t == "hover_load":
            self.preload_buildings_at(ev["lat"], ev["lon"])
        elif t == "measure":
            self._measure_points = ev["points"]
            self._render_measure()
        elif t == "probe":
            self.do_probe(ev["lat"], ev["lon"])
        elif t == "zone":
            self._on_zone_drawn(ev["kind"], ev["polygon"])
        elif t == "net_place":
            self.net_place_device(ev["lat"], ev["lon"])
        elif t == "net_select":
            self.net_select_device(ev["id"])
        elif t == "net_link_select":
            self.net_select_link(ev["id"])
        elif t == "net_connect":
            self.net_connect(ev["a"], ev["b"])
        elif t == "net_pdu":
            self.net_ping(ev["a"], ev["b"])
        elif t == "net_move":
            self.net_move_device(ev["id"], ev["lat"], ev["lon"])
        elif t == "azimuth":
            self.set_tower_azimuth(ev["id"], ev["azimuth"])
        elif t == "tower_move":
            self.set_tower_position(ev["id"], ev["lat"], ev["lon"])
        elif t == "snmp":
            self.snmp_open(ev.get("dev") or (f"T{ev['tower']}" if ev.get("tower") is not None else None))
        elif t == "mode":
            if ev.get("mode") in self._mode_actions:
                self._mode_actions[ev["mode"]].setChecked(True)

    def set_mode(self, mode):
        self._mode = mode
        self._js(f"api.setMapMode('{mode}');")
        names = {"navigate": "Навигация", "place": "Размещение вышек", "measure": "Линейка",
                 "probe": "Замер сигнала", "zone_algo": "Рисование участка", "zone_forbidden": "Рисование запретной зоны",
                 "device": "Размещение устройств", "cable": "Прокладка кабеля"}
        self.lbl_mode.setText(f"Режим: {names[mode]}")
        if mode == "measure":
            self.tabs.setCurrentIndex(2)
        elif mode == "probe":
            self.tabs.setCurrentIndex(1)
        elif mode in ("device", "cable", "pdu"):
            self.tabs.setCurrentWidget(self.net_page)
            self.left_tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------ #
    #  Здания
    # ------------------------------------------------------------------ #
    def _keep(self, worker):
        """Держит ссылку на поток, пока он не завершится."""
        self._threads.add(worker)
        worker.finished.connect(lambda w=worker: self._threads.discard(w))
        return worker

    def preload_buildings_at(self, lat, lon):
        """Предзагрузка зданий под курсором — только когда пользователь собирается ставить вышку/мерить.

        Раньше запросы шли при любом движении мыши; публичные Overpass отвечали отказом (429/504),
        включалась пауза «нет сети», и первая вышка считалась вообще без зданий.
        """
        if self._mode not in ("place", "probe") or not self.chk_buildings.isChecked():
            return
        now = time.time()
        if now - self._last_hover_time < 1.2 or self.building_data.is_covered(lat, lon, BUILDINGS_NEED_M):
            return
        self._last_hover_time = now
        self._hover_point = (lat, lon)
        self._start_fetch()

    def _request_buildings(self, points):
        for p in points:
            if all(geo.haversine_m(p[0], p[1], q[0], q[1]) > 150.0 for q in self._fetch_queue):
                self._fetch_queue.append(p)
        self._start_fetch()

    def _start_fetch(self):
        if self._fetch_busy:
            return
        pts = self._fetch_queue + ([self._hover_point] if self._hover_point else [])
        self._fetch_queue, self._hover_point = [], None
        pts = [p for p in pts if not self.building_data.is_covered(p[0], p[1], BUILDINGS_NEED_M)]
        if not pts:
            return
        self._fetch_busy = True
        w = self._fetch_worker = self._keep(BuildingFetchWorker(pts, self.building_data))
        w.area_done.connect(self._on_buildings_area)
        w.finished.connect(self._on_fetch_finished)
        self._update_buildings_label()
        w.start()

    def _on_buildings_area(self, ok):
        self._update_buildings_label()
        if ok:
            self._recalc_pending()
        elif any(t.buildings_pending for t in self.towers):
            self.statusBar().showMessage("НЕТ СЕТИ: здания OpenStreetMap не загружены — зоны рассчитаны без них "
                                         f"({self.building_data.last_error or 'сервер не ответил'}). F5 — повторить.")

    def _on_fetch_finished(self):
        self._fetch_busy = False
        self._start_fetch()
        self._recalc_pending()

    def _recalc_pending(self):
        """Пересчитать вышки, которые ждали загрузки зданий в своём районе."""
        if not self.chk_buildings.isChecked():
            return
        queued = {j.get("db_id") for j in self._job_queue}
        jobs = []
        for t in self.towers:
            if t.db_id in queued:
                continue
            if not (t.buildings_pending or not t.used_buildings):
                continue
            if not self.building_data.is_covered(t.lat, t.lon, BUILDINGS_NEED_M):
                continue
            mount = getattr(t, "mount", None) or {}
            if mount.get("type") == "ground":
                try:
                    bld_h = float(self.building_data.heights_at([t.lat], [t.lon])[0])
                    if bld_h > 0.0:
                        t.mount = {"type": "building", "id": None, "offset": 3.0, "bld_h": bld_h}
                        t.height = bld_h + 3.0
                except Exception:
                    pass
            jobs.append(self._job_from_tower(t))
        if jobs:
            self._enqueue(jobs)

    def _start_cache_loading(self):
        self._cache_worker = self._keep(CacheWorker(self.building_data))
        self._cache_worker.done.connect(self._on_cache_loaded)
        self._cache_worker.start()

    def _on_cache_loaded(self, n):
        self._update_buildings_label()
        self._recalc_stale()

    def _recalc_stale(self):
        """Пересчитать только вышки, чьи сохранённые зоны устарели.

        Раньше при каждом запуске пересчитывались все вышки (а потом обновлялась вся карта и сеть),
        хотя в БД уже лежали зоны, посчитанные с теми же зданиями. Теперь — только если сменилась
        модель/настройки или вышка ждала загрузки зданий.
        """
        flags = self._model_flags()
        jobs = [self._job_from_tower(t) for t in self.towers if t.needs_recalc(*flags)]
        if jobs:
            self._enqueue(jobs)

    def _update_buildings_label(self):
        self.lbl_buildings.setText(f"Здания: {self.building_data.status}")

    # ------------------------------------------------------------------ #
    #  Типы вышек
    # ------------------------------------------------------------------ #
    def _setting_bool(self, key, default):
        v = self.settings.value(key, default)
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")

    def _model_option_changed(self, key, value):
        self.settings.setValue(key, bool(value))
        self.recalc_all()

    def _model_flags(self):
        return self.chk_buildings.isChecked(), self.chk_fresnel.isChecked(), self.chk_pattern.isChecked()

    def _update_type_summary(self, *_):
        d = self.tower_types.get(self.active_type_combo.currentText())
        if not d:
            self.type_summary.setText("")
            return
        sector = "круговая" if d["beamwidth"] >= 360 else f"сектор {d['beamwidth']:.0f}°, азимут {d['azimuth']:.0f}°"
        self.type_summary.setText(f"{d['freq']:.0f} МГц · {d['power']:.1f} Вт · {d['height']:.0f} м · {sector} · "
                                  f"стена {d['wall_loss']:.0f} дБ")

    def show_add_tower_dialog(self):
        dlg = CreateTypeDialog(COLOR_MAP, parent=self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        if not data["name"]:
            QMessageBox.warning(self, "Ошибка", "Введите название типа.")
            return
        if data["name"] in self.tower_types:
            QMessageBox.warning(self, "Ошибка", "Тип с таким именем уже существует.")
            return
        self.tower_types[data["name"]] = {k: data[k] for k in PARAM_KEYS} | {
            "color": data["color"], "model_path": None, "scale": 1.0}
        self.db.set_meta("types", self.tower_types)
        self.active_type_combo.addItem(data["name"])
        self.active_type_combo.setCurrentText(data["name"])
        self._update_panel_types_list()

    def delete_current_tower_type(self):
        name = self.active_type_combo.currentText()
        if not name or name not in self.tower_types:
            return
        if len(self.tower_types) <= 1:
            QMessageBox.warning(self, "Удаление типа", "Нельзя удалить единственный оставшийся тип вышки.")
            return
        if QMessageBox.question(self, "Удаление типа", f"Удалить тип вышки «{name}»?") != QMessageBox.StandardButton.Yes:
            return
        del self.tower_types[name]
        self.db.set_meta("types", self.tower_types)
        self.active_type_combo.clear()
        self.active_type_combo.addItems(list(self.tower_types.keys()))
        self._update_panel_types_list()
        self.statusBar().showMessage(f"Тип вышки «{name}» удалён.")

    # ------------------------------------------------------------------ #
    #  Очередь расчётов
    # ------------------------------------------------------------------ #
    def _next_name(self):
        name = f"Вышка #{self.tower_counter}"
        self.tower_counter += 1
        return name

    def _find_nearest_structure(self, lat, lon, max_dist_m=25.0):
        best, best_d = None, max_dist_m
        for s in getattr(self, "structures", {}).values():
            d = ((s.lat - lat) ** 2 + (s.lon - lon) ** 2) ** 0.5 * 111320
            if d < best_d:
                best, best_d = s, d
        return best

    def place_tower_at_coords(self, lat, lon, pitch=0.0, bearing=0.0, bld_height=0.0):
        if self._mode != "place":
            return
        type_name = self.active_type_combo.currentText()
        type_data = self.tower_types.get(type_name)
        if not type_data:
            return

        # Если камера наклонена (3D вид, pitch > 8°), луч клика по крыше пересекает землю далеко позади здания.
        # Трассируем луч от точки пересечения земли обратно к камере, находя фактическую крышу здания:
        if pitch > 8.0 and self.building_data:
            import math
            cam_az = (bearing + 180.0) % 360.0
            rad_az = math.radians(cam_az)
            cos_az, sin_az = math.cos(rad_az), math.sin(rad_az)
            tan_pitch = math.tan(math.radians(pitch))
            m_lat = 111320.0
            m_lon = m_lat * math.cos(math.radians(lat))
            hit_lat, hit_lon, hit_h = None, None, 0.0
            for step_m in range(1, 160, 2):
                ray_z = step_m / tan_pitch
                test_lat = lat + (step_m * cos_az) / m_lat
                test_lon = lon + (step_m * sin_az) / m_lon
                try:
                    h = float(self.building_data.heights_at([test_lat], [test_lon])[0])
                    if h >= ray_z * 0.82:
                        hit_lat, hit_lon, hit_h = test_lat, test_lon, h
                        break
                except Exception:
                    pass
            if hit_lat is not None and hit_h > 0.0:
                lat, lon = hit_lat, hit_lon

        near_s = self._find_nearest_structure(lat, lon, max_dist_m=25.0)
        bld_h = 0.0
        if self.building_data:
            try:
                bld_h = float(self.building_data.heights_at([lat], [lon])[0])
            except Exception:
                bld_h = 0.0
        if bld_h <= 0.0 and bld_height > 0.0:
            bld_h = float(bld_height)
        if near_s:
            mount = {"type": "structure", "id": near_s.id, "offset": 0.0, "bld_h": 0.0}
            mount_h = near_s.height
        elif bld_h > 0.0:
            mount = {"type": "building", "id": None, "offset": 3.0, "bld_h": bld_h}
            mount_h = bld_h + 3.0
        else:
            # На земле: высота по умолчанию из типа вышки (или 30 м)
            default_h = float(type_data.get("height", 30.0))
            if default_h <= 0.0:
                default_h = 30.0
            mount = {"type": "ground", "id": None, "offset": 0.0, "bld_h": 0.0}
            mount_h = default_h

        job = self._job_from_type(self._next_name(), lat, lon, type_name, type_data)
        job["params"]["height"] = mount_h
        job["mount"] = mount
        if self.chk_buildings.isChecked() and self.building_data:
            if not self.building_data.is_covered(lat, lon, BUILDINGS_NEED_M):
                self.building_data.reset_cooldown()
                self._request_buildings([(lat, lon)])
        self._enqueue([job])

    @staticmethod
    def _job_from_type(name, lat, lon, type_name, type_data):
        return {"action": "new", "name": name, "lat": lat, "lon": lon, "type_name": type_name,
                "params": {k: type_data[k] for k in PARAM_KEYS}, "color": list(type_data["color"]),
                "scale": type_data.get("scale", 1.0)}

    @staticmethod
    def _job_from_tower(t):
        return {"action": "update", "name": t.name, "lat": t.lat, "lon": t.lon, "type_name": t.type_name,
                "params": t.params(), "color": list(t.color), "scale": t.scale, "db_id": t.db_id,
                "mount": getattr(t, "mount", None)}

    def _enqueue(self, jobs):
        upd_ids = {j.get("db_id") for j in jobs if j.get("action") == "update" and j.get("db_id") is not None}
        if upd_ids:
            self._job_queue = [j for j in self._job_queue
                               if not (j.get("action") == "update" and j.get("db_id") in upd_ids)]
        self._job_queue.extend(jobs)
        self._start_next()

    def _start_next(self):
        if self._calc_busy or not self._job_queue:
            return
        jobs, self._job_queue = self._job_queue, []
        self._calc_busy = True
        use_bd, fresnel, pattern = self._model_flags()
        self._worker = self._keep(CalcWorker(jobs, self.building_data, use_bd, fresnel, pattern))
        self._worker.progress.connect(lambda s: self.statusBar().showMessage(s))
        self._worker.tower_done.connect(self._on_tower_done)
        self._worker.all_done.connect(self._on_calc_finished)
        self.statusBar().showMessage("Расчёт…")
        self._worker.start()

    def _on_tower_done(self, tower, job):
        if job.get("mount") is not None:
            tower.mount = job["mount"]
        if job["action"] == "update" and tower.db_id is not None:
            idx = next((i for i, t in enumerate(self.towers) if t.db_id == tower.db_id), None)
            if idx is None:                        # вышку успели удалить, пока шёл пересчёт
                return
            self.towers[idx] = tower
            self.db.update_tower(tower.db_id, tower.to_dict())
        else:
            tower.db_id = self.db.save_tower(tower.to_dict())
            self.towers.append(tower)
            self.selected_id = tower.db_id
            if job.get("src_id") is not None:
                self._import_ids[job["src_id"]] = tower.db_id
            self._bump_counter(tower.name)
            t_id = tower.db_id
            t_name = tower.name
            def undo_place():
                self.db.delete_tower(t_id)
                self.towers = [x for x in self.towers if x.db_id != t_id]
                if self.selected_id == t_id:
                    self.selected_id = None
                self._refresh_all()
            self.push_undo(undo_place, f"Размещение «{t_name}»")

    def _bump_counter(self, name):
        """Счётчик имён «Вышка #N» не должен выдавать уже занятые номера (импорт, алгоритм)."""
        tail = name.split("#")[-1]
        if name.startswith("Вышка #") and tail.isdigit():
            self.tower_counter = max(self.tower_counter, int(tail) + 1)

    def _apply_pending_network(self):
        """Сеть из импортированного проекта: у вышек новые id, БС сопоставляются по старому id (или имени)."""
        data, self._pending_network = getattr(self, "_pending_network", None), None
        ids, self._import_ids = self._import_ids, {}
        if not data:
            return
        by_name = {t.name: t.db_id for t in self.towers}
        remap, devices = {}, []
        for d in data.get("devices", []):
            if d.get("tower_id") is not None:
                new = ids.get(d["tower_id"], by_name.get(d.get("name")))
                if new is None:
                    continue
                remap[d["id"]] = f"T{new}"
                d = dict(d, id=f"T{new}", tower_id=new)
            devices.append(d)
        keep = {d["id"] for d in devices}
        links = []
        for l in data.get("links", []):
            l = dict(l, a_dev=remap.get(l.get("a_dev"), l.get("a_dev")), b_dev=remap.get(l.get("b_dev"), l.get("b_dev")))
            if l["a_dev"] in keep and l["b_dev"] in keep:
                links.append(l)
        try:
            self.net = Network.from_dict(dict(data, devices=devices, links=links))
        except (TypeError, KeyError, ValueError) as exc:
            QMessageBox.warning(self, "Импорт", f"Сеть из проекта не прочитана: {exc}")

    def _on_calc_finished(self, info):
        self._calc_busy = False
        if getattr(self, "_pending_network", None) and not self._job_queue:
            self._apply_pending_network()
        self._refresh_all()
        use_bd = self.chk_buildings.isChecked()
        if info["count"] == 1:
            t = self._current_tower()
            extra = ""
            if use_bd:
                extra = (", здания загружаются…" if info["pending"] else
                         ", здания учтены" if (t and t.used_buildings) else ", зданий в зоне нет")
            msg = f"Рассчитано за {info['calc_ms']:.1f} мс{extra}"
        else:
            msg = f"Пересчитано вышек: {info['count']} за {info['total_ms']:.0f} мс"
            if use_bd and info["pending"]:
                msg += f"; для {info['pending']} загружаются здания…"
        if info["errors"]:
            msg += f" — ошибки расчёта: {'; '.join(info['errors'][:3])}"
        self.statusBar().showMessage(msg)
        self._update_buildings_label()
        if use_bd and info["pending"]:
            self._request_buildings([(t.lat, t.lon) for t in self.towers if t.buildings_pending])
        self._start_next()

    def set_tower_azimuth(self, tid, azimuth):
        """Азимут, выставленный перетаскиванием ручки на карте."""
        t = next((x for x in self.towers if x.db_id == tid), None)
        if t is None:
            return
        if abs((azimuth - t.azimuth + 180.0) % 360.0 - 180.0) < 0.5:
            self._push_towers()                    # без изменений — сбросить предпросмотр на карте
            return
        old_az = t.azimuth
        t_name = t.name
        def undo_az():
            self.set_tower_azimuth(tid, old_az)
        self.push_undo(undo_az, f"Поворот «{t_name}»")
        job = self._job_from_tower(t)
        job["params"]["azimuth"] = float(azimuth) % 360.0
        self._enqueue([job])

    def set_tower_position(self, tid, lat, lon):
        """Вышку перетащили по карте: пересчитать зоны на новом месте (кабели БС поедут следом)."""
        t = next((x for x in self.towers if x.db_id == tid), None)
        if t is None:
            return
        if geo.haversine_m(t.lat, t.lon, lat, lon) < 0.3:
            self._push_towers()
            self._push_net()
            return
        old_lat, old_lon = t.lat, t.lon
        t_name = t.name
        def undo_pos():
            self.set_tower_position(tid, old_lat, old_lon)
        self.push_undo(undo_pos, f"Перемещение «{t_name}»")
        t.lat, t.lon = float(lat), float(lon)
        self.db.update_tower(t.db_id, t.to_dict())
        self.net_after_towers_changed()
        job = self._job_from_tower(t)
        self._enqueue([job])

    def recalc_all(self):
        if not self.towers:
            return
        if self.building_data:
            self.building_data.reset_cooldown()
            if self.chk_buildings.isChecked():
                missing = [t for t in self.towers
                           if not self.building_data.is_covered(t.lat, t.lon, BUILDINGS_NEED_M)]
                if missing:
                    self._request_buildings([(t.lat, t.lon) for t in missing])
        self._enqueue([self._job_from_tower(t) for t in self.towers])

    # ------------------------------------------------------------------ #
    #  Список вышек и информация
    # ------------------------------------------------------------------ #
    def _current_tower(self):
        return next((t for t in self.towers if t.db_id == self.selected_id), None)

    def _refresh_all(self, sync_net=True):
        self._refresh_table()
        self._push_towers()
        self._render_info_panels()
        self._update_stats()
        self._update_action_states()
        self.lbl_towers.setText(f"Вышек: {len(self.towers)}")
        if sync_net:
            self.net_after_towers_changed()

    def _refresh_table(self):
        self._block_table = True
        self.tower_table.setRowCount(len(self.towers))
        for r, t in enumerate(self.towers):
            for c, text in enumerate((t.name, t.type_name or "—", f"{t.freq:.0f}", f"{t.r_weak:.3f}")):
                self.tower_table.setItem(r, c, QTableWidgetItem(text))
            if t.db_id == self.selected_id:
                self.tower_table.selectRow(r)
        if self._current_tower() is None:
            self.tower_table.clearSelection()
        self._block_table = False

    def _on_table_selection(self):
        if self._block_table:
            return
        rows = self.tower_table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self.towers):
            self.select_tower_by_id(self.towers[rows[0].row()].db_id)

    def select_tower_by_id(self, tid):
        self.selected_id = tid
        self._block_table = True
        self.tower_table.clearSelection()
        for r, t in enumerate(self.towers):
            if t.db_id == tid:
                self.tower_table.selectRow(r)
        self._block_table = False
        self._js(f"api.selectTower({json.dumps(tid)});")
        self.net_on_tower_selected(tid)
        self._render_info_panels()
        self._update_action_states()
        if tid is not None:
            self.tabs.setCurrentIndex(0)

    def _update_action_states(self):
        has = self._current_tower() is not None
        self.act_edit.setEnabled(has)
        self.act_delete.setEnabled(has)
        self.btn_tower_snmp.setEnabled(has)
        self.act_recalc.setEnabled(bool(self.towers))
        self.btn_run_algo.setEnabled(self.zone_algo is not None)

    def _html_table(self, rows):
        dim = self.th["dim"]
        body = "".join(f"<tr><td style='color:{dim}; padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f"<table cellpadding='2'>{body}</table>"

    def _render_info_panels(self):
        t = self._current_tower()
        if t is None:
            self.tower_info.setHtml("<p style='color:#888'>Выберите вышку на карте или в таблице для редактирования параметров.</p>")
            if hasattr(self, "ed_tower_name"):
                self._block_tower_edits = True
                try:
                    self.ed_tower_name.setText("")
                    self.ed_tower_name.setEnabled(False)
                    self.ed_tower_type.setEnabled(False)
                    self.ed_tower_coords.setText("")
                    self.ed_tower_coords.setEnabled(False)
                    self.ed_tower_height.setEnabled(False)
                    self.ed_tower_freq.setEnabled(False)
                    self.ed_tower_power.setEnabled(False)
                    self.ed_tower_tilt.setEnabled(False)
                    self.ed_tower_wall_loss.setEnabled(False)
                    self.ed_tower_azimuth.setEnabled(False)
                    self.ed_tower_beamwidth.setEnabled(False)
                    self.btn_tower_apply.setEnabled(False)
                    self.btn_tower_del_panel.setEnabled(False)
                    self.lbl_tower_eirp.setText("—")
                    if hasattr(self, "lbl_mount_status"):
                        self.lbl_mount_status.setText("—")
                        self.btn_tower_mount.setEnabled(False)
                    if hasattr(self, "cmb_tower_antenna_model"):
                        self.cmb_tower_antenna_model.setEnabled(False)
                        self.cmb_tower_antenna_model.blockSignals(True)
                        self.cmb_tower_antenna_model.setCurrentIndex(0)
                        self.cmb_tower_antenna_model.blockSignals(False)
                finally:
                    self._block_tower_edits = False
        else:
            sector = "круговая" if t.beamwidth >= 360 else f"{t.beamwidth:.0f}°"
            zone_rows = []
            cols = ("#2ecc71", "#a3d93b", "#f1c40f", "#e76432")
            radii = (t.r_green, t.r_exc, t.r_good, t.r_weak)
            for i, (name, thr) in enumerate(THRESHOLDS):
                zone_rows.append((f"<span style='color:{cols[i]}'>■</span> {ZONE_LABELS_RU[name]} (≥ {thr} дБм)",
                                  f"{radii[i]:.3f} км · {t.areas[i]:.3f} км²"))
            calc = f"{t.calc_ms:.1f} мс" if t.calc_ms else "из БД"
            bld = "учтены" if t.used_buildings else ("загружаются" if t.buildings_pending else "не учтены")
            model = ", ".join(x for x, on in (("дифракция Френеля", t.fresnel), ("лепестки антенны", t.pattern)) if on)
            self.tower_info.setHtml(
                f"<h3 style='margin:0 0 6px 0'>{t.name}</h3>"
                + f"<p style='margin:0 0 2px 0'><b>Расчёт:</b> {calc}, здания {bld}" + (f"; {model}" if model else "") + "</p>"
                + "<p style='margin:4px 0 2px 0'><b>Зоны покрытия:</b></p>" + self._html_table(zone_rows))
            if hasattr(self, "ed_tower_name"):
                self._block_tower_edits = True
                try:
                    self.ed_tower_name.setEnabled(True)
                    self.ed_tower_name.setText(t.name)
                    self.ed_tower_type.setEnabled(True)
                    self._update_panel_types_list(t.type_name)
                    self.ed_tower_coords.setEnabled(True)
                    self.ed_tower_coords.setText(f"{t.lat:.6f}, {t.lon:.6f}")
                    self.ed_tower_height.setEnabled(True)
                    self.ed_tower_height.setValue(float(t.height))
                    self.ed_tower_freq.setEnabled(True)
                    self.ed_tower_freq.setValue(float(t.freq))
                    self.ed_tower_power.setEnabled(True)
                    self.ed_tower_power.setValue(float(t.power))
                    self.lbl_tower_eirp.setText(f"{t.eirp_dbm:.1f} дБм")
                    self.ed_tower_tilt.setEnabled(True)
                    self.ed_tower_tilt.setValue(float(t.tilt))
                    self.ed_tower_wall_loss.setEnabled(True)
                    self.ed_tower_wall_loss.setValue(float(t.wall_loss))
                    self.ed_tower_azimuth.setEnabled(True)
                    self.ed_tower_azimuth.setValue(int(t.azimuth))
                    self.ed_tower_beamwidth.setEnabled(True)
                    self.ed_tower_beamwidth.setValue(int(t.beamwidth))
                    self.btn_tower_apply.setEnabled(True)
                    self.btn_tower_del_panel.setEnabled(True)
                    if hasattr(self, "lbl_mount_status"):
                        mount = getattr(t, "mount", {}) or {"type": "ground"}
                        m_type = mount.get("type", "ground")
                        if m_type == "building":
                            bld_h = mount.get("bld_h", 0.0)
                            off = mount.get("offset", 3.0)
                            self.lbl_mount_status.setText(f"🏢 Здание ({bld_h:.1f} м + {off:.1f} м = {t.height:.1f} м)")
                        elif m_type == "structure":
                            s_id = mount.get("id")
                            s = getattr(self, "structures", {}).get(s_id) if s_id else None
                            s_name = s.name if s else "Каркас"
                            self.lbl_mount_status.setText(f"🗼 {s_name} (подвес {t.height:.1f} м)")
                        else:
                            self.lbl_mount_status.setText("⚠️ Не закреплена (на земле, 0 м)")
                        self.btn_tower_mount.setEnabled(True)
                    if hasattr(self, "cmb_tower_antenna_model"):
                        self.cmb_tower_antenna_model.setEnabled(True)
                        self._sync_antenna_dropdown(t.type_name)
                finally:
                    self._block_tower_edits = False
        self._render_measure()
        self._render_probe()

    def _update_panel_types_list(self, current_name=None):
        if not hasattr(self, "ed_tower_type"):
            return
        self.ed_tower_type.blockSignals(True)
        self.ed_tower_type.clear()
        self.ed_tower_type.addItem("— (пользовательский)")
        for name in sorted(self.tower_types):
            self.ed_tower_type.addItem(name)
        if current_name and current_name in self.tower_types:
            self.ed_tower_type.setCurrentText(current_name)
        else:
            self.ed_tower_type.setCurrentIndex(0)
        self.ed_tower_type.blockSignals(False)

    def _on_tower_panel_type_changed(self, idx):
        if getattr(self, "_block_tower_edits", False):
            return
        name = self.ed_tower_type.currentText()
        if name in self.tower_types:
            preset = self.tower_types[name]
            self._block_tower_edits = True
            try:
                self.ed_tower_freq.setValue(float(preset.get("freq", 1800.0)))
                self.ed_tower_power.setValue(float(preset.get("power", 20.0)))
                self.ed_tower_height.setValue(float(preset.get("height", 30.0)))
                self.ed_tower_tilt.setValue(float(preset.get("tilt", 0.0)))
                self.ed_tower_wall_loss.setValue(float(preset.get("wall_loss", 20.0)))
                self.ed_tower_azimuth.setValue(int(preset.get("azimuth", 0.0)))
                self.ed_tower_beamwidth.setValue(int(preset.get("beamwidth", 360.0)))
                p_dbm = geo.w_to_dbm(float(preset.get("power", 20.0)))
                self.lbl_tower_eirp.setText(f"{p_dbm + 14.0:.1f} дБм")
            finally:
                self._block_tower_edits = False

    def _update_tower_panel_eirp(self, val):
        p_dbm = geo.w_to_dbm(val)
        if hasattr(self, "lbl_tower_eirp"):
            self.lbl_tower_eirp.setText(f"{p_dbm + 14.0:.1f} дБм")

    def _populate_antenna_dropdown(self):
        if not hasattr(self, "cmb_tower_antenna_model") or not hasattr(self, "db") or not getattr(self.db, "conn", None):
            return
        import antenna_catalog
        antennas = antenna_catalog.load_all_antennas(self.db.conn)
        self.cmb_tower_antenna_model.blockSignals(True)
        self.cmb_tower_antenna_model.clear()
        self.cmb_tower_antenna_model.addItem("— Выбрать из каталога вышек —", None)
        for a in sorted(antennas, key=lambda x: (x.manufacturer, x.model)):
            title = f"{a.manufacturer} {a.model} ({a.freq_mhz:.0f} МГц, {a.gain_dbi:.1f} дБи)"
            self.cmb_tower_antenna_model.addItem(title, a)
        self.cmb_tower_antenna_model.blockSignals(False)

    def _sync_antenna_dropdown(self, type_name):
        if not hasattr(self, "cmb_tower_antenna_model"):
            return
        self.cmb_tower_antenna_model.blockSignals(True)
        try:
            target = (type_name or "").strip().lower()
            found_idx = 0
            if target:
                for i in range(1, self.cmb_tower_antenna_model.count()):
                    a = self.cmb_tower_antenna_model.itemData(i)
                    if not a:
                        continue
                    if (a.model.lower() in target or target in a.model.lower()
                            or f"{a.manufacturer} {a.model}".lower() in target
                            or target in a.display_name.lower()):
                        found_idx = i
                        break
            self.cmb_tower_antenna_model.setCurrentIndex(found_idx)
        finally:
            self.cmb_tower_antenna_model.blockSignals(False)

    def _on_antenna_model_selected(self, idx):
        if getattr(self, "_block_tower_edits", False):
            return
        if not hasattr(self, "cmb_tower_antenna_model") or idx <= 0:
            return
        a = self.cmb_tower_antenna_model.itemData(idx)
        if not a:
            return
        self._block_tower_edits = True
        try:
            self.ed_tower_freq.setValue(float(a.freq_mhz))
            self.ed_tower_power.setValue(float(a.tx_power_w))
            self.ed_tower_beamwidth.setValue(int(a.beamwidth_h))
            self.ed_tower_wall_loss.setValue(float(a.wall_loss_db))
            p_dbm = geo.w_to_dbm(float(a.tx_power_w))
            self.lbl_tower_eirp.setText(f"{p_dbm + a.gain_dbi:.1f} дБм")
        finally:
            self._block_tower_edits = False

        preset_name = f"{a.manufacturer} {a.model}"
        t = self._current_tower()
        if preset_name not in self.tower_types:
            self.tower_types[preset_name] = {
                "name": preset_name,
                "freq": a.freq_mhz,
                "power": a.tx_power_w,
                "height": t.height if t else 30.0,
                "tilt": t.tilt if t else 4.0,
                "wall_loss": a.wall_loss_db,
                "color": [62, 139, 255, 255] if "5G" in a.category else [255, 140, 0, 255],
                "model_path": None,
                "scale": 1.0,
                "azimuth": t.azimuth if t else 0.0,
                "beamwidth": a.beamwidth_h,
            }
            self.db.set_meta("types", self.tower_types)
            self._update_panel_types_list(preset_name)
        else:
            self._update_panel_types_list(preset_name)

        if t is not None:
            self.statusBar().showMessage(f"Выбрана модель «{a.manufacturer} {a.model}» ({a.freq_mhz:.0f} МГц, {a.gain_dbi:.1f} дБи). Нажмите «Применить» для сохранения.")
        else:
            self.statusBar().showMessage(f"Выбрана модель «{a.manufacturer} {a.model}» из каталога.")

    def _apply_tower_panel_edits(self):
        t = self._current_tower()
        if t is None:
            return
        name = self.ed_tower_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Имя вышки не может быть пустым.")
            return
        coords_str = self.ed_tower_coords.text().strip()
        lat, lon = t.lat, t.lon
        try:
            parts = [float(x.strip()) for x in coords_str.replace(";", ",").split(",")]
            if len(parts) >= 2:
                lat, lon = parts[0], parts[1]
        except (ValueError, IndexError):
            pass
        type_name = self.ed_tower_type.currentText()
        if type_name.startswith("—"):
            type_name = t.type_name
        params = {
            "freq": self.ed_tower_freq.value(),
            "power": self.ed_tower_power.value(),
            "height": self.ed_tower_height.value(),
            "tilt": self.ed_tower_tilt.value(),
            "wall_loss": self.ed_tower_wall_loss.value(),
            "azimuth": float(self.ed_tower_azimuth.value()),
            "beamwidth": float(self.ed_tower_beamwidth.value()),
        }
        old_job = self._job_from_tower(t)
        t_name = t.name
        def undo_panel():
            self._enqueue([old_job])
        self.push_undo(undo_panel, f"Свойства «{t_name}»")
        self._enqueue([{"action": "update", "name": name, "lat": lat, "lon": lon,
                        "type_name": type_name, "params": params, "mount": getattr(t, "mount", None),
                        "color": t.color, "scale": t.scale, "db_id": t.db_id}])
        self.statusBar().showMessage(f"Параметры вышки «{name}» обновлены.")

    def open_antenna_catalog_dialog(self):
        """Открывает базу данных и каталог серийных промышленных антенн."""
        import antenna_catalog
        dlg = antenna_catalog.AntennaCatalogDialog(self.db.conn, parent=self)
        if dlg.exec() and dlg.selected_antenna:
            a = dlg.selected_antenna
            preset_name = f"{a.manufacturer} {a.model}"
            self.tower_types[preset_name] = {
                "name": preset_name,
                "freq": a.freq_mhz,
                "power": a.tx_power_w,
                "height": 30.0,
                "tilt": 4.0,
                "wall_loss": a.wall_loss_db,
                "color": [62, 139, 255, 255] if "5G" in a.category else [255, 140, 0, 255],
                "model_path": None,
                "scale": 1.0,
                "azimuth": 0.0,
                "beamwidth": a.beamwidth_h,
            }
            self.db.set_meta("types", self.tower_types)
            self.active_type_combo.clear()
            self.active_type_combo.addItems(list(self.tower_types.keys()))
            self.active_type_combo.setCurrentText(preset_name)
            self._update_panel_types_list()
            self._populate_antenna_dropdown()
            self._sync_antenna_dropdown(preset_name)
            t = self._current_tower()
            if t is not None:
                old_job = self._job_from_tower(t)
                t_name = t.name
                def undo_ant():
                    self._enqueue([old_job])
                self.push_undo(undo_ant, f"Антенна «{t_name}»")
                p = t.params()
                p["freq"] = a.freq_mhz
                p["power"] = a.tx_power_w
                p["beamwidth"] = a.beamwidth_h
                p["wall_loss"] = a.wall_loss_db
                self._enqueue([{"action": "update", "name": t.name, "lat": t.lat, "lon": t.lon,
                                "type_name": a.display_name, "params": p, "mount": getattr(t, "mount", None),
                                "color": t.color, "scale": t.scale, "db_id": t.db_id}])
                self.statusBar().showMessage(f"Модель антенны «{a.model}» применена к вышке «{t.name}» и добавлена в типы.")
            else:
                self.statusBar().showMessage(f"Выбран тип антенны: «{a.display_name}».")

    def open_create_structure_dialog(self, lat=None, lon=None):
        """Создание новой опорной конструкции (каркаса, мачты или столба)."""
        import structures
        if lat is None or lon is None:
            t = self._current_tower()
            lat = t.lat if t else 47.0101
            lon = t.lon if t else 28.8638
        dlg = structures.CreateStructureDialog(lat, lon, len(self.structures), parent=self)
        if dlg.exec() and dlg.structure:
            s = dlg.structure
            self.structures[s.id] = s
            structures.save_structure(self.db.conn, s)
            self.statusBar().showMessage(f"Создан каркас: «{s.name}» (высота {s.height:.0f} м).")
            s_id = s.id
            s_name = s.name
            def undo_struct():
                if s_id in self.structures:
                    del self.structures[s_id]
                    structures.delete_structure(self.db.conn, s_id)
                    self._push_towers()
            self.push_undo(undo_struct, f"Создание каркаса «{s_name}»")
            # Если рядом есть выбранная вышка — предлагаем/привязываем к каркасу
            t = self._current_tower()
            if t is not None and abs(t.lat - s.lat) < 0.0005 and abs(t.lon - s.lon) < 0.0005:
                t.mount = {"type": "structure", "id": s.id, "offset": 0.0, "bld_h": 0.0}
                p = t.params()
                p["height"] = s.height
                self._enqueue([{"action": "update", "name": t.name, "lat": t.lat, "lon": t.lon,
                                "type_name": t.type_name, "params": p, "mount": t.mount,
                                "color": t.color, "scale": t.scale, "db_id": t.db_id}])

    def open_mount_manager_dialog(self):
        """Управление прикреплением антенны к зданию или каркасу вышки."""
        t = self._current_tower()
        if t is None:
            QMessageBox.information(self, "Монтаж", "Сначала выберите вышку на карте или в таблице.")
            return
        import structures
        mount = getattr(t, "mount", {}) or {"type": "ground", "id": None, "offset": 0.0, "bld_h": 0.0}
        bld_h = 0.0
        if self.building_data:
            try:
                bld_h = float(self.building_data.heights_at([t.lat], [t.lon])[0])
            except Exception:
                bld_h = 0.0
        dlg = structures.MountManagerDialog(
            t.lat, t.lon, self.structures,
            building_roof_h=bld_h,
            current_mount_type=mount.get("type", "ground"),
            current_mount_id=mount.get("id"),
            current_offset=mount.get("offset", 0.0),
            parent=self
        )
        if dlg.exec():
            old_job = self._job_from_tower(t)
            t_name = t.name
            def undo_mount():
                self._enqueue([old_job])
            self.push_undo(undo_mount, f"Монтаж «{t_name}»")
            t.mount = {
                "type": dlg.result_mount_type,
                "id": dlg.result_mount_id,
                "offset": dlg.result_offset,
                "bld_h": bld_h if dlg.result_mount_type == "building" else 0.0
            }
            p = t.params()
            p["height"] = max(0.0, dlg.result_total_height)
            self._enqueue([{"action": "update", "name": t.name, "lat": t.lat, "lon": t.lon,
                            "type_name": t.type_name, "params": p, "mount": t.mount,
                            "color": t.color, "scale": t.scale, "db_id": t.db_id}])
            self.statusBar().showMessage(f"Монтаж вышки «{t.name}» обновлён: высота {p['height']:.1f} м.")

    # ------------------------------------------------------------------ #
    #  Редактирование / удаление
    # ------------------------------------------------------------------ #
    def edit_selected_tower(self):
        t = self._current_tower()
        if t is None:
            QMessageBox.information(self, "Инфо", "Выберите вышку на карте или в таблице.")
            return
        dlg = EditTowerDialog(self.tower_types, t, COLOR_MAP, parent=self)
        if not dlg.exec():
            return
        d = dlg.get_data()
        if not d["name"]:
            QMessageBox.warning(self, "Ошибка", "Имя не может быть пустым.")
            return
        old_job = self._job_from_tower(t)
        t_name = t.name
        def undo_edit():
            self._enqueue([old_job])
        self.push_undo(undo_edit, f"Редактирование «{t_name}»")
        self._enqueue([{"action": "update", "name": d["name"], "lat": d["lat"], "lon": d["lon"],
                        "type_name": d["type_name"], "params": {k: d[k] for k in PARAM_KEYS},
                        "color": d["color"], "scale": t.scale, "db_id": t.db_id}])

    def delete_selected_tower(self):
        t = self._current_tower()
        if t is None:
            QMessageBox.information(self, "Инфо", "Выберите вышку для удаления.")
            return
        if QMessageBox.question(self, "Удаление", f"Удалить '{t.name}'?") != QMessageBox.StandardButton.Yes:
            return
        t_data = t.to_dict()
        t_mount = getattr(t, "mount", None)
        t_name = t.name
        t_db_id = t.db_id
        cables_backup = [dict(c) for c in getattr(self.net, "cables", [])
                         if c.get("from_id") == t.name or c.get("to_id") == t.name or
                            c.get("from_dev") == f"T{t_db_id}" or c.get("to_dev") == f"T{t_db_id}"]

        def undo_delete():
            new_id = self.db.save_tower(t_data)
            restored = Tower.from_dict(t_data)
            restored.db_id = new_id
            if t_mount is not None:
                restored.mount = t_mount
            self.towers.append(restored)
            self.selected_id = new_id
            for cb in cables_backup:
                if cb.get("from_dev") == f"T{t_db_id}":
                    cb["from_dev"] = f"T{new_id}"
                if cb.get("to_dev") == f"T{t_db_id}":
                    cb["to_dev"] = f"T{new_id}"
                self.net.cables.append(cb)
            self._refresh_all()
            self._enqueue([self._job_from_tower(restored)])

        self.push_undo(undo_delete, f"Удаление «{t_name}»")

        self.db.delete_tower(t.db_id)
        self.towers = [x for x in self.towers if x.db_id != t.db_id]
        self.selected_id = None
        self._refresh_all()

    def clear_map_only(self):
        self.towers.clear()
        self.selected_id = None
        self._refresh_all(sync_net=False)             # вышки остаются в БД — их кабели в сети не трогаем
        self.statusBar().showMessage("Карта очищена; в БД вышки сохранены до следующей очистки/удаления")

    def clear_all(self):
        if QMessageBox.question(self, "Очистка", "Удалить все вышки из БД?") != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_all()
        self.towers.clear()
        self.selected_id = None
        self.tower_counter = 1
        self.net = Network()
        self._refresh_all()

    def load_state_from_db(self):
        self.net_load_from_db()
        try:
            import structures
            self.structures = structures.load_all_structures(self.db.conn)
        except Exception:
            self.structures = {}
        saved = self.db.get_meta("types")
        if saved:
            self.tower_types.update(saved)
            self.active_type_combo.clear()
            self.active_type_combo.addItems(list(self.tower_types.keys()))
        zones = self.db.get_meta("zones") or {}
        self.zone_algo = zones.get("algo")
        self.zone_forbidden = zones.get("forbidden", [])
        for row in self.db.load_all_towers():
            self.towers.append(Tower.from_db_row(row))        # зоны берутся из БД — мгновенно
        nums = [int(t.name.split("#")[-1]) for t in self.towers
                if t.name.startswith("Вышка #") and t.name.split("#")[-1].isdigit()]
        self.tower_counter = max(nums, default=0) + 1
        self._refresh_all()
        self._update_zone_info()

    # ------------------------------------------------------------------ #
    #  Зоны и алгоритмы
    # ------------------------------------------------------------------ #
    def _on_zone_drawn(self, kind, polygon):
        if kind == "algo":
            self.zone_algo = polygon
        else:
            self.zone_forbidden.append(polygon)
        self._save_zones()
        self._push_zones()
        self._update_zone_info()
        self._update_stats()
        self._update_action_states()
        self._mode_actions["navigate"].setChecked(True)

    def _save_zones(self):
        self.db.set_meta("zones", {"algo": self.zone_algo, "forbidden": self.zone_forbidden})

    def clear_zones(self):
        self.zone_algo, self.zone_forbidden = None, []
        self._save_zones()
        self._push_zones()
        self._update_zone_info()
        self._update_stats()
        self._update_action_states()

    def _update_zone_info(self):
        if self.zone_algo:
            text = f"Участок: {geo.polygon_area_m2(self.zone_algo) / 1e6:.3f} км²"
        else:
            text = "Участок не задан"
        if self.zone_forbidden:
            text += f"\nЗапретных зон: {len(self.zone_forbidden)}"
        self.zone_info.setText(text)

    def _update_stats(self):
        s = coverage.coverage_stats(self.towers, self.zone_algo, self.zone_forbidden) if self.zone_algo else None
        if not s:
            self.stats_label.setText("Задайте участок, чтобы увидеть долю покрытия.")
            return
        cols = {"green": "#2ecc71", "excellent": "#a3d93b", "good": "#f1c40f", "weak": "#e76432", "none": "#8a93a0"}
        labels = {"green": "Зелёная", "excellent": "Отличная", "good": "Средняя", "weak": "Слабая", "none": "Нет сигнала"}
        rows = "".join(f"<tr><td><span style='color:{cols[k]}'>■</span> {labels[k]}</td>"
                       f"<td align='right'>&nbsp;{s[k] * 100:.1f}%</td></tr>"
                       for k in ("green", "excellent", "good", "weak", "none"))
        self.stats_label.setText(
            f"<b>Покрыто: {s['covered'] * 100:.1f}%</b> &nbsp;(средняя и лучше: {s['good_or_better'] * 100:.1f}%)"
            f"<table cellpadding='1'>{rows}</table>")

    def run_algorithm(self):
        if not self.zone_algo:
            QMessageBox.information(self, "Алгоритм", "Сначала нарисуйте участок («Участок алгоритма»).")
            return
        if self._algo_worker and self._algo_worker.isRunning():
            return
        type_name = self.active_type_combo.currentText()
        task = auto_placement.PlacementTask(
            area=[tuple(p) for p in self.zone_algo], forbidden=[[tuple(p) for p in f] for f in self.zone_forbidden],
            tower_type=dict(self.tower_types[type_name]), max_towers=self.max_towers_spin.value(),
            building_data=self.building_data if self.chk_buildings.isChecked() else None)
        func = auto_placement.ALGORITHMS[self.algo_combo.currentText()]
        self._algo_worker = AlgoWorker(func, task)
        self._algo_worker.done.connect(lambda pts: self._on_algo_done(pts, type_name))
        self._algo_worker.failed.connect(self._on_algo_failed)
        self.btn_run_algo.setEnabled(False)
        self.statusBar().showMessage("Алгоритм работает…")
        self._algo_worker.start()

    def _on_algo_done(self, points, type_name):
        self.btn_run_algo.setEnabled(True)
        td = self.tower_types[type_name]
        jobs = []
        for p in points or []:
            lat, lon = (p["lat"], p["lon"]) if isinstance(p, dict) else (p[0], p[1])
            jobs.append(self._job_from_type(self._next_name(), lat, lon, type_name, td))
        self.statusBar().showMessage(f"Алгоритм вернул вышек: {len(jobs)}")
        if jobs:
            self._enqueue(jobs)

    def _on_algo_failed(self, msg, not_implemented):
        self.btn_run_algo.setEnabled(self.zone_algo is not None)
        self.statusBar().showMessage("Алгоритм не выполнен")
        (QMessageBox.information if not_implemented else QMessageBox.warning)(self, "Алгоритм", msg)

    # ------------------------------------------------------------------ #
    #  Линейка и замер сигнала
    # ------------------------------------------------------------------ #
    def _render_measure(self):
        pts = self._measure_points
        if len(pts) < 2:
            self.measure_info.setHtml("<p>Кликайте по карте — каждая точка добавляет отрезок. "
                                      "ПКМ или Backspace — убрать последнюю точку.</p>")
            return
        freq = (self.tower_types.get(self.active_type_combo.currentText()) or {"freq": 1800.0})["freq"]
        rows, total = [], 0.0
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            d = geo.haversine_m(a[0], a[1], b[0], b[1])
            total += d
            az = geo.bearing_deg(a[0], a[1], b[0], b[1])
            rows.append(f"<tr><td>{i}</td><td>{geo.format_distance(d)}</td><td>{geo.format_bearing(az)}</td>"
                        f"<td>{geo.format_bearing((az + 180) % 360)}</td></tr>")
        direct = geo.haversine_m(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
        self.measure_info.setHtml(
            "<table cellpadding='3'><tr><th>№</th><th>Длина</th><th>Азимут</th><th>Обратный</th></tr>"
            + "".join(rows) + "</table>"
            + f"<p><b>Итого по ломаной: {geo.format_distance(total)}</b><br>"
            f"По прямой (первая → последняя): {geo.format_distance(direct)}<br>"
            f"Потери в свободном пространстве на {freq:.0f} МГц (по прямой): "
            f"{geo.fspl_db(max(direct, 1.0), freq):.1f} дБ<br>"
            f"Задержка распространения: {direct / 299792458.0 * 1e6:.2f} мкс</p>")

    def do_probe(self, lat, lon):
        if not self.towers:
            self.signal_info.setHtml("<p>Сначала поставьте хотя бы одну вышку.</p>")
            return
        bd = self.building_data if self.chk_buildings.isChecked() else None
        results = [(t, link_analysis(t, lat, lon, bd)) for t in self.towers]
        results.sort(key=lambda tr: (tr[1]["rssi_dbm"] is None, -(tr[1]["rssi_dbm"] or 0.0)))
        self._probe_point, self._probe_results = (lat, lon), results
        best = results[0][1]
        text = "вне сектора" if best["rssi_dbm"] is None else f"{best['rssi_dbm']:.0f} дБм"
        self._js(f"api.setProbe({lat}, {lon}, {json.dumps(text)}, {json.dumps(QUALITY_COLORS[best['quality']])});")
        self._render_probe()
        self.btn_profile.setEnabled(True)

    def _render_probe(self):
        if not self._probe_results:
            return
        lat, lon = self._probe_point
        rows = []
        for i, (t, r) in enumerate(self._probe_results):
            q = r["quality"]
            level = "вне сектора" if r["rssi_dbm"] is None else f"<b>{r['rssi_dbm']:.1f} дБм</b>"
            mark = "★ " if i == 0 and r["rssi_dbm"] is not None else ""
            rows.append(f"<tr><td>{mark}{t.name}</td><td>{geo.format_distance(r['distance_m'])}</td>"
                        f"<td>{r['azimuth']:.0f}°</td><td>{r['walls']}</td>"
                        f"<td style='color:{QUALITY_COLORS[q]}'>{level}</td></tr>")
        best = self._probe_results[0][1]
        verdict = ZONE_LABELS_RU.get(best["quality"], "нет покрытия") if best["rssi_dbm"] is not None else "нет покрытия"
        self.signal_info.setHtml(
            f"<p><b>Точка:</b> {lat:.6f}, {lon:.6f}<br><b>Лучший сервер:</b> "
            f"<span style='color:{QUALITY_COLORS[best['quality']]}'>{verdict}</span></p>"
            "<table cellpadding='3'><tr><th align='left'>Вышка</th><th>Расст.</th><th>Азим.</th><th>Стен</th>"
            f"<th>Уровень</th></tr>{''.join(rows)}</table>")

    def show_link_profile(self):
        if not self._probe_results:
            return
        cur = self._current_tower()
        pick = next(((t, r) for t, r in self._probe_results if cur and t.db_id == cur.db_id), self._probe_results[0])
        LinkProfileDialog(pick[0].name, pick[1], self.th, self).exec()

    # ------------------------------------------------------------------ #
    #  Инструменты и файлы
    # ------------------------------------------------------------------ #
    def goto_coords(self):
        text, ok = QInputDialog.getText(self, "Перейти к координатам", "Широта, долгота:", text="47.0101, 28.8638")
        if not ok:
            return
        try:
            lat, lon = geo.parse_latlon(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Координаты", str(exc))
            return
        self._js(f"api.flyTo({lat}, {lon}, 17);")

    def show_calculator(self):
        t = self._current_tower()
        freq = t.freq if t else self.tower_types[self.active_type_combo.currentText()]["freq"]
        CalculatorDialog(freq, self).exec()

    def _save_dialog(self, title, default, flt):
        path, _ = QFileDialog.getSaveFileName(self, title, str(BASE_DIR / default), flt)
        return path

    def _write(self, path, text):
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.statusBar().showMessage(f"Сохранено: {path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка записи", str(exc))

    def export_project(self):
        path = self._save_dialog("Экспорт проекта", "zonem_project.json", "Проект ZoneM (*.json)")
        if path:
            self._write(path, exporters.project_to_json(self.tower_types, self.towers, self.zone_algo, self.zone_forbidden,
                                                        self.net.to_dict()))

    def export_csv(self):
        path = self._save_dialog("Экспорт таблицы вышек", "towers.csv", "CSV (*.csv)")
        if path:
            self._write(path, exporters.towers_to_csv(self.towers))

    def export_geojson(self):
        path = self._save_dialog("Экспорт покрытия", "coverage.geojson", "GeoJSON (*.geojson *.json)")
        if path:
            self._write(path, json.dumps(exporters.coverage_geojson(self.towers, self.zone_algo, self.zone_forbidden)))

    def export_png(self):
        path = self._save_dialog("Снимок карты", "map.png", "PNG (*.png)")
        if not path:
            return
        if self.web_view.grab().save(path):
            self.statusBar().showMessage(f"Сохранено: {path}")
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось сохранить снимок.")

    def import_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт проекта", str(BASE_DIR), "Проект ZoneM (*.json)")
        if not path:
            return
        try:
            data = exporters.project_from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Импорт", f"Не удалось прочитать проект: {exc}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Импорт")
        box.setText(f"В проекте вышек: {len(data.get('towers', []))}. Что сделать с текущими?")
        b_replace = box.addButton("Заменить", QMessageBox.ButtonRole.DestructiveRole)
        b_add = box.addButton("Добавить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_replace, b_add):
            return
        self._pending_network = None
        self._import_ids = {}
        if clicked is b_replace:
            self.db.clear_all()
            self.towers.clear()
            self.selected_id = None
            self._pending_network = data.get("network")
        self.tower_types.update(data.get("types") or {})
        self.db.set_meta("types", self.tower_types)
        self.active_type_combo.clear()
        self.active_type_combo.addItems(list(self.tower_types.keys()))
        zones = data.get("zones") or {}
        if zones.get("algo"):
            self.zone_algo = zones["algo"]
        self.zone_forbidden = (self.zone_forbidden if clicked is b_add else []) + zones.get("forbidden", [])
        self._save_zones()
        self._push_zones()
        self._update_zone_info()
        defaults = DEFAULT_PRESETS["Низкая микросота"]
        jobs = []
        for d in data.get("towers", []):
            jobs.append({"action": "new", "name": d["name"], "lat": d["lat"], "lon": d["lon"],
                         "type_name": d.get("type_name"), "src_id": d.get("id"),
                         "params": {k: d.get(k, defaults[k]) for k in PARAM_KEYS},
                         "color": d.get("color", [255, 0, 0, 255]), "scale": d.get("scale", 1.0)})
        self._refresh_all()
        if jobs:
            self._enqueue(jobs)

    def show_help(self):
        QMessageBox.information(self, "Горячие клавиши", (
            "Ctrl+1 — навигация / выбор вышки кликом\nCtrl+2 — ставить вышки\nCtrl+3 — линейка\n"
            "Ctrl+4 — замер сигнала\nCtrl+5 — устройство, Ctrl+6 — кабель, Ctrl+7 — PDU (ping), Ctrl+8 — SNMP\nCtrl+L — тест нагрузки\nEsc — вернуться к навигации\nDel — удалить выбранную вышку\n"
            "F5 — пересчитать все вышки\nCtrl+G — перейти к координатам\nCtrl+K — калькулятор\n"
            "Ctrl+T — тема\n\nНа карте: Backspace / ПКМ — отменить последнюю точку, "
            "Enter или двойной клик — завершить участок.\n\nАзимут секторной вышки: выберите её и перетащите синюю ручку на карте.\n"
            "Вышку и устройство можно перетащить мышью в режиме «Навигация».\n"
            "Двойной клик по вышке/устройству — SNMP-опрос."))

    def show_about(self):
        QMessageBox.about(self, "О программе",
                          "<b>ZoneM</b> — планирование радиопокрытия.<br>Модель: логарифмические потери на трассе + "
                          "многостеночное затухание в зданиях (OpenStreetMap).<br>Подробности — в README.md.")

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.net_flush_persist()
        self._job_queue.clear()
        self._fetch_queue.clear()
        self._hover_point = None
        for w in (self._load_worker, self._fetch_worker):
            if w:
                w.cancelled = True                   # загрузка зданий прерывается за ~0.25 с
        for w in set(self._threads) | {self._algo_worker, self._load_worker}:
            if w and w.isRunning():
                if not w.wait(3000):                 # сеть может висеть до таймаута — не ждём вечно
                    w.terminate()
                    w.wait()
        self.db.close()
        super().closeEvent(event)


_ERROR_LOG = BASE_DIR / "zonem_errors.log"
_shown_errors = set()


def report_exception(exc_type, exc, tb):
    """Печатает ошибку, пишет в zonem_errors.log и показывает окно (раз на каждую ошибку).

    Важно: пока sys.excepthook стандартный, PyQt6 при любой необработанной ошибке в слоте
    вызывает abort() — программа закрывается с кодом 0xC0000409 без объяснений.
    """
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    print(text, file=sys.stderr)
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n")
    except OSError:
        pass
    key = text.strip().splitlines()[-1]
    if key not in _shown_errors and QApplication.instance() is not None:
        _shown_errors.add(key)
        QMessageBox.critical(None, "Ошибка в программе",
                             f"{key}\n\nПодробности записаны в {_ERROR_LOG.name}. Программа продолжит работу.")


def main():
    import faulthandler
    faulthandler.enable()                      # при настоящем падении Qt/WebEngine покажет, где это случилось
    sys.excepthook = report_exception
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = CellTower3DApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
