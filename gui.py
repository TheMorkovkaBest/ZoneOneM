import json
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, QThread, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
                             QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
                             QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolBar,
                             QToolButton, QVBoxLayout, QWidget)

import auto_placement
import coverage
import exporters
import geo
import theme as theming
import netcatalog
from buildings import BuildingData
from calculations import THRESHOLDS, ZONE_LABELS_RU, link_analysis
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
class HoverWorker(QThread):
    """Предзагрузка зданий вокруг курсора."""

    def __init__(self, lat, lon, building_data):
        super().__init__()
        self.lat, self.lon, self.building_data = lat, lon, building_data

    def run(self):
        if self.building_data:
            self.building_data.fetch(self.lat, self.lon, radius_m=1000)


class CacheWorker(QThread):
    """Чтение дискового кэша зданий при старте."""
    done = pyqtSignal(int)

    def __init__(self, building_data):
        super().__init__()
        self.building_data = building_data

    def run(self):
        self.done.emit(self.building_data.load_cache())


class CalcWorker(QThread):
    """Считает пачку вышек. Здания докачиваются при необходимости, сам расчёт — миллисекунды."""
    progress = pyqtSignal(str)
    tower_done = pyqtSignal(object, object)      # Tower, job
    all_done = pyqtSignal(dict)

    def __init__(self, jobs, building_data, use_buildings):
        super().__init__()
        self.jobs, self.building_data, self.use_buildings = jobs, building_data, use_buildings

    def run(self):
        t0 = time.perf_counter()
        buildings_ok, calc_ms = True, 0.0
        for i, job in enumerate(self.jobs):
            bd = None
            if self.use_buildings:
                if not self.building_data.is_covered(job["lat"], job["lon"], 600.0):
                    self.progress.emit("Загрузка зданий из OpenStreetMap…")
                buildings_ok = self.building_data.ensure_loaded(job["lat"], job["lon"], need_m=600.0) and buildings_ok
                bd = self.building_data
            p = job["params"]
            tower = Tower(job["name"], job["lat"], job["lon"], p["freq"], p["power"], p["height"], p["tilt"],
                          p["wall_loss"], job["color"], scale=job.get("scale", 1.0), building_data=bd,
                          azimuth=p["azimuth"], beamwidth=p["beamwidth"],
                          type_name=job.get("type_name"), db_id=job.get("db_id"))
            calc_ms += tower.calc_ms
            self.tower_done.emit(tower, job)
            if len(self.jobs) > 1:
                self.progress.emit(f"Расчёт вышек: {i + 1}/{len(self.jobs)}")
        self.all_done.emit({"count": len(self.jobs), "buildings_ok": buildings_ok,
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
        self._worker = self._hover_worker = self._cache_worker = self._algo_worker = None
        self._last_hover_time = 0.0
        self._needs_recalc = False
        self._block_table = False
        self._probe_point = None
        self._probe_results = []
        self._measure_points = []
        self._icon_actions = []
        self._mode_actions = {}

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
        splitter.setSizes([290, 900, 360])
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
        defs = [("navigate", "Навигация", "navigate", "Ctrl+1", "Перемещение по карте и выбор вышки кликом"),
                ("place", "Поставить вышку", "tower", "Ctrl+2", "Клик по карте ставит вышку выбранного типа"),
                ("measure", "Линейка", "ruler", "Ctrl+3", "Расстояние и азимут между точками"),
                ("probe", "Замер сигнала", "probe", "Ctrl+4", "Уровень сигнала в точке и профиль трассы"),
                ("zone_algo", "Участок алгоритма", "polygon", None, "Нарисовать участок для автоматической расстановки"),
                ("zone_forbidden", "Запретная зона", "ban", None, "Нарисовать зону, где вышки ставить нельзя"),
                ("device", "Устройство", "server", "Ctrl+5", "Поставить сетевое устройство (тип — стрелка рядом или палитра «Сеть»)"),
                ("cable", "Кабель", "link", "Ctrl+6", "Соединить два устройства (тип — стрелка рядом или палитра «Сеть»)"),
                ("pdu", "PDU (ping)", "send", "Ctrl+7", "Послать пакет от устройства к устройству и увидеть путь")]
        for name, text, icon, sc, tip in defs:
            act = self._act(text, icon, checkable=True, shortcut=sc, tip=tip)
            act.toggled.connect(lambda checked, n=name: checked and self.set_mode(n))
            grp.addAction(act)
            self._mode_actions[name] = act

        self.act_edit = self._act("Редактировать", "edit", self.edit_selected_tower, tip="Изменить выбранную вышку")
        self.act_delete = self._act("Удалить", "trash", self.delete_selected_tower, shortcut="Del", tip="Удалить выбранную вышку")
        self.act_recalc = self._act("Пересчитать всё", "refresh", self.recalc_all, shortcut="F5", tip="Пересчитать зоны всех вышек")
        self.act_net_auto = self._act("Авто-IP", "sliders", self.net_autoconfigure, tip="Раздать IP и построить маршруты")
        self.act_bom = self._act("Спецификация", "list", self.net_export_bom, tip="Спецификация оборудования и кабелей (CSV)")
        self.act_load = self._act("Тест нагрузки", "zap", self.run_load_test, shortcut="Ctrl+L",
                                  tip="Абоненты, трафик и загрузка сети: запуск/остановка")
        self.act_load_clear = self._act("Сброс", "stop", self.clear_load_results, tip="Убрать результаты теста с карты")
        self.act_goto = self._act("Координаты…", "goto", self.goto_coords, shortcut="Ctrl+G", tip="Перейти к координатам")
        self.act_calc = self._act("Калькулятор", "calc", self.show_calculator, shortcut="Ctrl+K", tip="Радиокалькулятор")
        self.act_theme = self._act("Сменить тему", "moon", self.toggle_theme, shortcut="Ctrl+T")
        self.act_import = self._act("Импорт проекта…", "upload", self.import_project, tip="Загрузить проект (JSON)")
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
        m = mb.addMenu("Инструменты")
        for name in ("navigate", "place", "measure", "probe"):
            m.addAction(self._mode_actions[name])
        m.addSeparator()
        for a in (self.act_recalc, self.act_goto, self.act_calc):
            m.addAction(a)
        m = mb.addMenu("Вид")
        m.addAction(self.act_theme)
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
        g.add_action(self.act_goto)
        g = bar.add_group("Радиосеть")
        g.add_action(m["place"])
        g.add_action(self.act_edit)
        g.add_action(self.act_delete)
        g.add_action(self.act_recalc)
        g = bar.add_group("Транспортная сеть")
        self._device_menu = self.build_device_menu()
        self._cable_menu = self.build_cable_menu()
        g.add_action(m["device"], self._device_menu)
        g.add_action(m["cable"], self._cable_menu)
        g.add_action(m["pdu"])
        g.add_action(self.act_net_auto)
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
        exp_btn.setText("Экспорт")
        exp_btn.setToolTip("Экспорт данных")
        exp_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        exp_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        exp_btn.setIconSize(QSize(26, 26))
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
        btn_new = QPushButton("Новый тип…")
        btn_new.clicked.connect(self.show_add_tower_dialog)
        lay.addWidget(btn_new)
        self.chk_buildings = QCheckBox("Учитывать затухание в зданиях")
        self.chk_buildings.setChecked(True)
        self.chk_buildings.toggled.connect(lambda _: self.recalc_all())
        lay.addWidget(self.chk_buildings)
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
        self.tower_info = QTextBrowser()
        self.tower_info.setPlaceholderText("Выберите вышку на карте или в таблице")
        tabs.addTab(self.tower_info, "Вышка")

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
        split.setSizes([260, 520])
        lay.addWidget(split, stretch=1)
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(520)
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
        self._js(f"api.setTheme('{self.theme_name}');")
        self._js(f"api.setBase('{self.base_name or self.theme_name}');")     # JS пропускает повтор той же подложки

    # ------------------------------------------------------------------ #
    #  Связь с картой
    # ------------------------------------------------------------------ #
    def _js(self, code):
        if self._map_ready:
            self.web_view.page().runJavaScript(code)

    def _on_map_loaded(self, ok):
        self._map_ready = bool(ok)
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
        self._js(f"api.setTowers({json.dumps([t.to_js() for t in self.towers])});")
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
            self.place_tower_at_coords(ev["lat"], ev["lon"])
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
        elif t == "mode":
            if ev.get("mode") in self._mode_actions:
                self._mode_actions[ev["mode"]].setChecked(True)

    def set_mode(self, mode):
        self._mode = mode
        self._js(f"api.setMapMode('{mode}');")
        names = {"navigate": "Навигация", "place": "Размещение вышек", "measure": "Линейка",
                 "probe": "Замер сигнала", "zone_algo": "Рисование участка", "zone_forbidden": "Рисование запретной зоны",
                 "device": "Размещение устройств", "cable": "Прокладка кабеля", "pdu": "PDU (ping)"}
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
    def preload_buildings_at(self, lat, lon):
        now = time.time()
        if now - self._last_hover_time < 1.2 or not self.chk_buildings.isChecked():
            return
        if self._hover_worker and self._hover_worker.isRunning():
            return
        self._last_hover_time = now
        self._hover_worker = HoverWorker(lat, lon, self.building_data)
        self._hover_worker.finished.connect(self._update_buildings_label)
        self._hover_worker.start()

    def _start_cache_loading(self):
        self._cache_worker = CacheWorker(self.building_data)
        self._cache_worker.done.connect(self._on_cache_loaded)
        self._cache_worker.start()

    def _on_cache_loaded(self, n):
        self._update_buildings_label()
        if n and (self._needs_recalc or self.towers):
            self.recalc_all()                      # вышки из БД пересчитываем с учётом загруженных зданий

    def _update_buildings_label(self):
        self.lbl_buildings.setText(f"Здания: {self.building_data.status}")

    # ------------------------------------------------------------------ #
    #  Типы вышек
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    #  Очередь расчётов
    # ------------------------------------------------------------------ #
    def _next_name(self):
        name = f"Вышка #{self.tower_counter}"
        self.tower_counter += 1
        return name

    def place_tower_at_coords(self, lat, lon):
        if self._mode != "place":
            return
        type_name = self.active_type_combo.currentText()
        type_data = self.tower_types.get(type_name)
        if not type_data:
            return
        self._enqueue([self._job_from_type(self._next_name(), lat, lon, type_name, type_data)])

    @staticmethod
    def _job_from_type(name, lat, lon, type_name, type_data):
        return {"action": "new", "name": name, "lat": lat, "lon": lon, "type_name": type_name,
                "params": {k: type_data[k] for k in PARAM_KEYS}, "color": list(type_data["color"]),
                "scale": type_data.get("scale", 1.0)}

    @staticmethod
    def _job_from_tower(t):
        return {"action": "update", "name": t.name, "lat": t.lat, "lon": t.lon, "type_name": t.type_name,
                "params": t.params(), "color": list(t.color), "scale": t.scale, "db_id": t.db_id}

    def _enqueue(self, jobs):
        self._job_queue.extend(jobs)
        self._start_next()

    def _start_next(self):
        if (self._worker and self._worker.isRunning()) or not self._job_queue:
            return
        jobs, self._job_queue = self._job_queue, []
        self._worker = CalcWorker(jobs, self.building_data, self.chk_buildings.isChecked())
        self._worker.progress.connect(lambda s: self.statusBar().showMessage(s))
        self._worker.tower_done.connect(self._on_tower_done)
        self._worker.all_done.connect(self._on_calc_finished)
        self.statusBar().showMessage("Расчёт…")
        self._worker.start()

    def _on_tower_done(self, tower, job):
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

    def _apply_pending_network(self):
        """Сеть из импортированного проекта: id вышек новые, поэтому БС сопоставляются по имени."""
        data, self._pending_network = getattr(self, "_pending_network", None), None
        if not data:
            return
        by_name = {t.name: t.db_id for t in self.towers}
        remap, devices = {}, []
        for d in data.get("devices", []):
            if d.get("tower_id") is not None:
                new = by_name.get(d["name"])
                if new is None:
                    continue
                remap[d["id"]] = f"T{new}"
                d = dict(d, id=f"T{new}", tower_id=new)
            devices.append(d)
        keep = {d["id"] for d in devices}
        links = []
        for l in data.get("links", []):
            l = dict(l, a_dev=remap.get(l["a_dev"], l["a_dev"]), b_dev=remap.get(l["b_dev"], l["b_dev"]))
            if l["a_dev"] in keep and l["b_dev"] in keep:
                links.append(l)
        self.net = Network.from_dict(dict(data, devices=devices, links=links))

    def _on_calc_finished(self, info):
        self._needs_recalc = False
        if getattr(self, "_pending_network", None) and not self._job_queue:
            self._apply_pending_network()
        self._refresh_all()
        if info["count"] == 1:
            t = self._current_tower()
            extra = ""
            if self.chk_buildings.isChecked():
                extra = ", здания учтены" if (t and t.used_buildings) else ", зданий в зоне нет"
            msg = f"Рассчитано за {info['calc_ms']:.1f} мс{extra}"
        else:
            msg = f"Пересчитано вышек: {info['count']} за {info['total_ms']:.0f} мс"
        if self.chk_buildings.isChecked() and not info["buildings_ok"]:
            msg += " — НЕТ СЕТИ: здания не загружены, расчёт без них"
        self.statusBar().showMessage(msg)
        self._update_buildings_label()
        self._start_next()

    def set_tower_azimuth(self, tid, azimuth):
        """Азимут, выставленный перетаскиванием ручки на карте."""
        t = next((x for x in self.towers if x.db_id == tid), None)
        if t is None:
            return
        if abs((azimuth - t.azimuth + 180.0) % 360.0 - 180.0) < 0.5:
            self._push_towers()                    # без изменений — сбросить предпросмотр на карте
            return
        job = self._job_from_tower(t)
        job["params"]["azimuth"] = float(azimuth) % 360.0
        self._enqueue([job])

    def recalc_all(self):
        if not self.towers:
            return
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
        self.act_recalc.setEnabled(bool(self.towers))
        self.btn_run_algo.setEnabled(self.zone_algo is not None)

    def _html_table(self, rows):
        dim = self.th["dim"]
        body = "".join(f"<tr><td style='color:{dim}; padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f"<table cellpadding='2'>{body}</table>"

    def _render_info_panels(self):
        t = self._current_tower()
        if t is None:
            self.tower_info.setHtml("")
        else:
            sector = "круговая" if t.beamwidth >= 360 else f"{t.beamwidth:.0f}°"
            zone_rows = []
            cols = ("#2ecc71", "#a3d93b", "#f1c40f", "#e76432")
            radii = (t.r_green, t.r_exc, t.r_good, t.r_weak)
            for i, (name, thr) in enumerate(THRESHOLDS):
                zone_rows.append((f"<span style='color:{cols[i]}'>■</span> {ZONE_LABELS_RU[name]} (≥ {thr} дБм)",
                                  f"{radii[i]:.3f} км · {t.areas[i]:.3f} км²"))
            calc = f"{t.calc_ms:.1f} мс" if t.calc_ms else "из БД"
            bld = "учтены" if t.used_buildings else "не учтены"
            self.tower_info.setHtml(
                f"<h3 style='margin:0 0 6px 0'>{t.name}</h3>"
                + self._html_table([
                    ("Тип", t.type_name or "—"), ("Координаты", f"{t.lat:.6f}, {t.lon:.6f}"),
                    ("Частота", f"{t.freq:.0f} МГц"),
                    ("Мощность", f"{t.power:.1f} Вт ({geo.w_to_dbm(t.power):.1f} дБм)"),
                    ("EIRP", f"{t.eirp_dbm:.1f} дБм"), ("Высота", f"{t.height:.1f} м"),
                    ("Наклон", f"{t.tilt:.1f}° (в модель не входит)"),
                    ("Потери на стену", f"{t.wall_loss:.1f} дБ"),
                    ("Азимут / луч", f"{t.azimuth:.0f}° / {sector}"),
                    ("Расчёт", f"{calc}, здания {bld}"),
                ])
                + "<p style='margin:8px 0 2px 0'><b>Зоны покрытия</b></p>" + self._html_table(zone_rows))
        self._render_measure()
        self._render_probe()

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
        saved = self.db.get_meta("types")
        if saved:
            self.tower_types.update(saved)
            self.active_type_combo.clear()
            self.active_type_combo.addItems(list(self.tower_types.keys()))
        zones = self.db.get_meta("zones") or {}
        self.zone_algo = zones.get("algo")
        self.zone_forbidden = zones.get("forbidden", [])
        for row in self.db.load_all_towers():
            self._needs_recalc |= not row["reach"]
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
                         "type_name": d.get("type_name"),
                         "params": {k: d.get(k, defaults[k]) for k in PARAM_KEYS},
                         "color": d.get("color", [255, 0, 0, 255]), "scale": d.get("scale", 1.0)})
        self._refresh_all()
        if jobs:
            self._enqueue(jobs)

    def show_help(self):
        QMessageBox.information(self, "Горячие клавиши", (
            "Ctrl+1 — навигация / выбор вышки кликом\nCtrl+2 — ставить вышки\nCtrl+3 — линейка\n"
            "Ctrl+4 — замер сигнала\nCtrl+5 — устройство, Ctrl+6 — кабель, Ctrl+7 — PDU (ping)\nCtrl+L — тест нагрузки\nEsc — вернуться к навигации\nDel — удалить выбранную вышку\n"
            "F5 — пересчитать все вышки\nCtrl+G — перейти к координатам\nCtrl+K — калькулятор\n"
            "Ctrl+T — тема\n\nНа карте: Backspace / ПКМ — отменить последнюю точку, "
            "Enter или двойной клик — завершить участок.\n\nАзимут секторной вышки: выберите её и перетащите синюю ручку на карте."))

    def show_about(self):
        QMessageBox.about(self, "О программе",
                          "<b>ZoneM</b> — планирование радиопокрытия.<br>Модель: логарифмические потери на трассе + "
                          "многостеночное затухание в зданиях (OpenStreetMap).<br>Подробности — в README.md.")

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self._job_queue.clear()
        if self._load_worker:
            self._load_worker.cancelled = True
        for w in (self._worker, self._hover_worker, self._cache_worker, self._algo_worker, self._load_worker):
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
