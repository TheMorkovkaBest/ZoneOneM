import sys
import json
import time
from PyQt6.QtCore import QUrl, QThread, pyqtSignal, Qt, QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit, QComboBox, QDoubleSpinBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog,
    QToolBar, QTextEdit, QDialog, QDialogButtonBox, QFormLayout
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage

from tower import Tower, TowerDB, DEFAULT_PRESETS, COLOR_MAP
from buildings import BuildingData


class HoverWorker(QThread):
    def __init__(self, lat, lon, building_data):
        super().__init__()
        self.lat = lat
        self.lon = lon
        self.building_data = building_data

    def run(self):
        if self.building_data:
            self.building_data.fetch(self.lat, self.lon, radius_m=1000)


class CalcWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, name, lat, lon, type_data, building_data):
        super().__init__()
        self.name = name
        self.lat = lat
        self.lon = lon
        self.type_data = type_data
        self.building_data = building_data

    def run(self):
        tower = Tower(
            name=self.name, lat=self.lat, lon=self.lon,
            freq=self.type_data["freq"], power=self.type_data["power"],
            height=self.type_data["height"], tilt=self.type_data["tilt"],
            wall_loss=self.type_data["wall_loss"], color=self.type_data["color"],
            model_path=self.type_data["model_path"], scale=self.type_data["scale"],
            building_data=self.building_data,
            azimuth=self.type_data.get("azimuth", 0.0),
            beamwidth=self.type_data.get("beamwidth", 360.0)
        )
        self.done.emit(tower.to_dict())


class MapPage(QWebEnginePage):
    def __init__(self, parent_app, profile, parent_view):
        super().__init__(profile, parent_view)
        self.parent_app = parent_app

    def javaScriptConsoleMessage(self, level, msg, line, source):
        if msg.startswith("ADD_TOWER:"):
            _, lat, lon = msg.split(":")
            self.parent_app.place_tower_at_coords(float(lat), float(lon))
        elif msg.startswith("SELECT_TOWER:"):
            _, lat, lon = msg.split(":")
            self.parent_app.select_nearest_tower(float(lat), float(lon))
        elif msg.startswith("HOVER_LOAD:"):
            _, lat, lon = msg.split(":")
            self.parent_app.preload_buildings_at(float(lat), float(lon))


class CreateTypeDialog(QDialog):
    def __init__(self, color_map, preset=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать тип вышки")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Новый тип")
        layout.addRow("Название типа:", self.name_input)

        self.freq_input = QDoubleSpinBox()
        self.freq_input.setRange(100, 6000)
        self.freq_input.setValue(1800)
        self.freq_input.setSuffix(" МГц")
        layout.addRow("Частота:", self.freq_input)

        self.power_input = QDoubleSpinBox()
        self.power_input.setRange(0.1, 500)
        self.power_input.setValue(10)
        self.power_input.setSuffix(" Вт")
        layout.addRow("Мощность:", self.power_input)

        self.height_input = QDoubleSpinBox()
        self.height_input.setRange(1, 300)
        self.height_input.setValue(5)
        self.height_input.setSuffix(" м")
        layout.addRow("Высота подвеса:", self.height_input)

        self.tilt_input = QDoubleSpinBox()
        self.tilt_input.setRange(0, 90)
        self.tilt_input.setValue(6)
        self.tilt_input.setSuffix("°")
        layout.addRow("Наклон (Tilt):", self.tilt_input)

        self.wall_loss_input = QDoubleSpinBox()
        self.wall_loss_input.setRange(0, 50)
        self.wall_loss_input.setValue(20)
        self.wall_loss_input.setSuffix(" дБ")
        layout.addRow("Потери на стену:", self.wall_loss_input)

        self.azimuth_input = QDoubleSpinBox()
        self.azimuth_input.setRange(0, 360)
        self.azimuth_input.setValue(0)
        self.azimuth_input.setSuffix("°")
        layout.addRow("Азимут:", self.azimuth_input)

        self.beamwidth_input = QDoubleSpinBox()
        self.beamwidth_input.setRange(5, 360)
        self.beamwidth_input.setValue(360)
        self.beamwidth_input.setSuffix("°")
        layout.addRow("Ширина луча:", self.beamwidth_input)

        self.color_combo = QComboBox()
        self.color_combo.addItems(color_map.keys())
        layout.addRow("Цвет:", self.color_combo)

        if preset:
            self.name_input.setText(preset.get("name", ""))
            self.freq_input.setValue(preset.get("freq", 1800))
            self.power_input.setValue(preset.get("power", 10))
            self.height_input.setValue(preset.get("height", 5))
            self.tilt_input.setValue(preset.get("tilt", 6))
            self.wall_loss_input.setValue(preset.get("wall_loss", 20))
            self.azimuth_input.setValue(preset.get("azimuth", 0))
            self.beamwidth_input.setValue(preset.get("beamwidth", 360))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        return {
            "name": self.name_input.text().strip(),
            "freq": self.freq_input.value(),
            "power": self.power_input.value(),
            "height": self.height_input.value(),
            "tilt": self.tilt_input.value(),
            "wall_loss": self.wall_loss_input.value(),
            "azimuth": self.azimuth_input.value(),
            "beamwidth": self.beamwidth_input.value(),
            "color_name": self.color_combo.currentText()
        }


class EditTowerDialog(QDialog):
    def __init__(self, tower_types, tower_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактировать вышку")
        self.setMinimumWidth(350)

        layout = QFormLayout(self)

        self.name_input = QLineEdit(tower_dict["name"])
        layout.addRow("Имя:", self.name_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems(tower_types.keys())
        layout.addRow("Тип:", self.type_combo)

        self.lat_input = QDoubleSpinBox()
        self.lat_input.setRange(-90, 90)
        self.lat_input.setDecimals(6)
        self.lat_input.setValue(tower_dict["lat"])
        self.lat_input.setSuffix("°")
        layout.addRow("Широта:", self.lat_input)

        self.lon_input = QDoubleSpinBox()
        self.lon_input.setRange(-180, 180)
        self.lon_input.setDecimals(6)
        self.lon_input.setValue(tower_dict["lon"])
        self.lon_input.setSuffix("°")
        layout.addRow("Долгота:", self.lon_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_data(self):
        return {
            "name": self.name_input.text().strip(),
            "type": self.type_combo.currentText(),
            "lat": self.lat_input.value(),
            "lon": self.lon_input.value()
        }


class CellTower3DApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("3D Симулятор радиопокрытия")
        self.resize(1400, 900)

        self.db = TowerDB()
        self.building_data = BuildingData()
        self.towers = []
        self.tower_counter = 1
        self._worker = None
        self._hover_worker = None
        self._last_hover_time = 0
        self._map_mode = "normal"

        self.tower_types = dict(DEFAULT_PRESETS)

        self.init_ui()
        self.load_towers_from_db()

    def preload_buildings_at(self, lat, lon):
        now = time.time()
        if now - self._last_hover_time < 1.2:
            return
        if self._hover_worker and self._hover_worker.isRunning():
            return
        self._last_hover_time = now
        self._hover_worker = HoverWorker(lat, lon, self.building_data)
        self._hover_worker.start()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        toolbar = QToolBar("Главная")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        btn_create = QPushButton("Создать вышку")
        btn_create.clicked.connect(self.show_add_tower_dialog)
        toolbar.addWidget(btn_create)

        btn_select = QPushButton("Выбрать на карте")
        btn_select.setCheckable(True)
        btn_select.toggled.connect(self.toggle_select_mode)
        toolbar.addWidget(btn_select)

        btn_place = QPushButton("Ставить на карту")
        btn_place.setCheckable(True)
        btn_place.toggled.connect(self.toggle_place_mode)
        toolbar.addWidget(btn_place)

        toolbar.addSeparator()

        btn_delete = QPushButton("Удалить вышку")
        btn_delete.clicked.connect(self.delete_selected_tower)
        toolbar.addWidget(btn_delete)

        btn_edit = QPushButton("Редактировать")
        btn_edit.clicked.connect(self.edit_selected_tower)
        toolbar.addWidget(btn_edit)

        toolbar.addSeparator()

        btn_clear_map = QPushButton("Очистить карту")
        btn_clear_map.clicked.connect(self.clear_map_only)
        toolbar.addWidget(btn_clear_map)

        btn_clear_db = QPushButton("Очистить БД")
        btn_clear_db.clicked.connect(self.clear_all)
        toolbar.addWidget(btn_clear_db)

        toolbar.addSeparator()

        toolbar.addWidget(QLabel(" Тип: "))
        self.active_type_combo = QComboBox()
        self.active_type_combo.addItems(self.tower_types.keys())
        self.active_type_combo.setMinimumWidth(200)
        toolbar.addWidget(self.active_type_combo)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        left_sidebar = QWidget()
        left_sidebar.setFixedWidth(180)
        left_layout = QVBoxLayout(left_sidebar)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)

        left_title = QLabel("Алгоритмы")
        left_title.setFont(QFont("", 10, QFont.Weight.Bold))
        left_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(left_title)

        btn_run_algo = QPushButton("Запуск алгоритма")
        btn_run_algo.setMinimumHeight(40)
        left_layout.addWidget(btn_run_algo)

        btn_highlight_algo = QPushButton("Зона алгоритма")
        btn_highlight_algo.setMinimumHeight(40)
        left_layout.addWidget(btn_highlight_algo)

        btn_no_build = QPushButton("Запретная зона")
        btn_no_build.setMinimumHeight(40)
        left_layout.addWidget(btn_no_build)

        for _ in range(3):
            spacer = QPushButton("")
            spacer.setEnabled(False)
            spacer.setMinimumHeight(40)
            left_layout.addWidget(spacer)

        left_layout.addStretch()

        self.web_view = QWebEngineView()
        custom_page = MapPage(self, self.web_view.page().profile(), self.web_view)
        self.web_view.setPage(custom_page)

        right_sidebar = QWidget()
        right_sidebar.setFixedWidth(260)
        right_layout = QVBoxLayout(right_sidebar)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)

        info_title = QLabel("Информация")
        info_title.setFont(QFont("", 10, QFont.Weight.Bold))
        info_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(info_title)

        self.tower_info_text = QTextEdit()
        self.tower_info_text.setReadOnly(True)
        self.tower_info_text.setPlaceholderText("Выберите вышку на карте...")
        right_layout.addWidget(self.tower_info_text)

        signal_title = QLabel("Сигнал в точке")
        signal_title.setFont(QFont("", 9, QFont.Weight.Bold))
        right_layout.addWidget(signal_title)

        self.signal_info_text = QTextEdit()
        self.signal_info_text.setReadOnly(True)
        self.signal_info_text.setMaximumHeight(120)
        self.signal_info_text.setPlaceholderText("Наведите курсор на карту...")
        right_layout.addWidget(self.signal_info_text)

        table_title = QLabel("Список вышек")
        table_title.setFont(QFont("", 9, QFont.Weight.Bold))
        right_layout.addWidget(table_title)

        self.tower_table = QTableWidget(0, 3)
        self.tower_table.setHorizontalHeaderLabels(["Имя", "Частота", "Зона"])
        self.tower_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tower_table.setMaximumHeight(200)
        self.tower_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tower_table.clicked.connect(self._on_table_clicked)
        right_layout.addWidget(self.tower_table)

        extra_title = QLabel("Прочее")
        extra_title.setFont(QFont("", 9, QFont.Weight.Bold))
        right_layout.addWidget(extra_title)

        self.extra_info_text = QTextEdit()
        self.extra_info_text.setReadOnly(True)
        self.extra_info_text.setPlaceholderText("")
        right_layout.addWidget(self.extra_info_text)

        content_layout.addWidget(left_sidebar)
        content_layout.addWidget(self.web_view, stretch=1)
        content_layout.addWidget(right_sidebar)

        main_layout.addWidget(content)

        self.init_map_html()

    def toggle_select_mode(self, checked):
        if checked:
            self._map_mode = "select"
            self._set_place_btn_checked(False)
            self._set_js_mode("select")
        else:
            if self._map_mode == "select":
                self._map_mode = "normal"
                self._set_js_mode("normal")

    def toggle_place_mode(self, checked):
        if checked:
            self._map_mode = "place"
            self._set_select_btn_checked(False)
            self._set_js_mode("place")
        else:
            if self._map_mode == "place":
                self._map_mode = "normal"
                self._set_js_mode("normal")

    def _set_select_btn_checked(self, val):
        for btn in self.findChildren(QPushButton):
            if btn.text() == "Выбрать на карте":
                btn.blockSignals(True)
                btn.setChecked(val)
                btn.blockSignals(False)

    def _set_place_btn_checked(self, val):
        for btn in self.findChildren(QPushButton):
            if btn.text() == "Ставить на карту":
                btn.blockSignals(True)
                btn.setChecked(val)
                btn.blockSignals(False)

    def _set_js_mode(self, mode):
        self.web_view.page().runJavaScript(f"setMapMode('{mode}');")

    def select_nearest_tower(self, lat, lon):
        if not self.towers:
            return
        best_idx = -1
        best_dist = float('inf')
        for i, t in enumerate(self.towers):
            dlat = t["lat"] - lat
            dlon = t["lon"] - lon
            dist = (dlat * dlat + dlon * dlon)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx >= 0:
            self.tower_table.selectRow(best_idx)
            self._show_tower_info(best_idx)

    def _show_tower_info(self, idx):
        if idx < 0 or idx >= len(self.towers):
            self.tower_info_text.clear()
            return
        t = self.towers[idx]
        text = (
            f"Имя: {t['name']}\n"
            f"Частота: {t.get('freq', 1800)} МГц\n"
            f"Мощность: {t.get('power', 10)} Вт\n"
            f"Высота: {t.get('height', 5)} м\n"
            f"Наклон: {t.get('tilt', 6)}°\n"
            f"Потери на стену: {t.get('wall_loss', 20)} дБ\n"
            f"Азимут: {t.get('azimuth', 0)}°\n"
            f"Ширина луча: {t.get('beamwidth', 360)}°\n"
            f"Зона зелёная: {t.get('r_green', 0)} км\n"
            f"Зона отличная: {t.get('r_exc', 0)} км\n"
            f"Зона средняя: {t.get('r_good', 0)} км\n"
            f"Зона слабая: {t.get('r_weak', 0)} км"
        )
        self.tower_info_text.setText(text)

    def _on_table_clicked(self, index):
        self._show_tower_info(index.row())

    def show_add_tower_dialog(self):
        dlg = CreateTypeDialog(COLOR_MAP, parent=self)
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                QMessageBox.warning(self, "Ошибка", "Введите название типа.")
                return
            if data["name"] in self.tower_types:
                QMessageBox.warning(self, "Ошибка", "Тип с таким именем уже существует.")
                return

            self.tower_types[data["name"]] = {
                "freq": data["freq"],
                "power": data["power"],
                "height": data["height"],
                "tilt": data["tilt"],
                "wall_loss": data["wall_loss"],
                "color": COLOR_MAP[data["color_name"]],
                "model_path": None,
                "scale": 1.0,
                "azimuth": data["azimuth"],
                "beamwidth": data["beamwidth"]
            }
            self.active_type_combo.addItem(data["name"])
            self.active_type_combo.setCurrentText(data["name"])

    def place_tower_at_coords(self, lat, lon):
        if self._map_mode != "place":
            return
        if self._worker and self._worker.isRunning():
            return
        active_type_name = self.active_type_combo.currentText()
        type_data = self.tower_types.get(active_type_name)
        if not type_data:
            return
        tower_name = f"Вышка #{self.tower_counter}"
        self.tower_counter += 1
        self._worker = CalcWorker(tower_name, lat, lon, type_data, self.building_data)
        self._worker.done.connect(self._on_tower_ready)
        self._worker.start()

    def edit_selected_tower(self):
        selected = self.tower_table.currentRow()
        if selected < 0 or selected >= len(self.towers):
            QMessageBox.information(self, "Инфо", "Выберите вышку в таблице для редактирования.")
            return
        tower = self.towers[selected]
        dlg = EditTowerDialog(self.tower_types, tower, parent=self)
        if dlg.exec():
            data = dlg.get_data()
            type_data = self.tower_types.get(data["type"])
            if not type_data:
                return
            self.db.delete_tower(tower["name"])
            self.towers.pop(selected)
            self.tower_table.removeRow(selected)
            self._start_calc(data["name"], data["lat"], data["lon"], type_data)

    def delete_selected_tower(self):
        selected = self.tower_table.currentRow()
        if selected < 0 or selected >= len(self.towers):
            QMessageBox.information(self, "Инфо", "Выберите вышку для удаления.")
            return
        tower = self.towers[selected]
        reply = QMessageBox.question(self, "Удаление", f"Удалить '{tower['name']}'?")
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_tower(tower["name"])
            self.towers.pop(selected)
            self.tower_table.removeRow(selected)
            self.update_deck_gl()

    def _start_calc(self, name, lat, lon, type_data):
        if self._worker and self._worker.isRunning():
            return
        self._worker = CalcWorker(name, lat, lon, type_data, self.building_data)
        self._worker.done.connect(self._on_tower_ready)
        self._worker.start()

    def _on_tower_ready(self, tower_dict):
        self.towers.append(tower_dict)
        self.db.save_tower(tower_dict)

        row_idx = self.tower_table.rowCount()
        self.tower_table.insertRow(row_idx)
        self.tower_table.setItem(row_idx, 0, QTableWidgetItem(tower_dict["name"]))
        self.tower_table.setItem(row_idx, 1, QTableWidgetItem(f"{tower_dict.get('freq', 1800)} МГц"))
        self.tower_table.setItem(row_idx, 2, QTableWidgetItem(f"{tower_dict.get('r_weak', 0)} км"))

        self.update_deck_gl()

    def load_towers_from_db(self):
        rows = self.db.load_all_towers()
        for row in rows:
            tower = Tower.from_db_row(row)
            tower_dict = tower.to_dict()
            self.towers.append(tower_dict)

            row_idx = self.tower_table.rowCount()
            self.tower_table.insertRow(row_idx)
            self.tower_table.setItem(row_idx, 0, QTableWidgetItem(tower_dict["name"]))
            self.tower_table.setItem(row_idx, 1, QTableWidgetItem(f"{tower_dict.get('freq', 1800)} МГц"))
            self.tower_table.setItem(row_idx, 2, QTableWidgetItem(f"{tower_dict.get('r_weak', 0)} км"))

        if self.towers:
            self.tower_counter = len(self.towers) + 1
            self.update_deck_gl()

    def clear_map_only(self):
        self.towers.clear()
        self.tower_table.setRowCount(0)
        self.tower_counter = 1
        self.update_deck_gl()

    def clear_all(self):
        reply = QMessageBox.question(self, "Очистка", "Удалить все вышки из БД?")
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_all()
            self.towers.clear()
            self.tower_table.setRowCount(0)
            self.tower_counter = 1
            self.update_deck_gl()

    def update_deck_gl(self):
        towers_json = json.dumps(self.towers)
        self.web_view.page().runJavaScript(f"updateTowers({towers_json});")

    def init_map_html(self):
        html_code = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>3D Coverage Map</title>
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <script src="https://unpkg.com/@loaders.gl/core@3.3.1/dist/dist.min.js"></script>
    <script src="https://unpkg.com/@loaders.gl/gltf@3.3.1/dist/dist.min.js"></script>
    <script src="https://unpkg.com/deck.gl@8.9.0/dist.min.js"></script>

    <style>
        body { margin: 0; padding: 0; width: 100vw; height: 100vh; overflow: hidden; background: #1a1a2e; }
        #map { width: 100%; height: 100%; }
        .crosshair { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #0f0; font-size: 20px; font-weight: bold; pointer-events: none; z-index: 999; text-shadow: 0 0 4px #0f0; }
        #mode-indicator {
            position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
            padding: 6px 16px; border-radius: 4px; font-family: sans-serif; font-size: 13px;
            font-weight: bold; z-index: 999; pointer-events: none; display: none;
        }
        #mode-select { background: rgba(41, 128, 185, 0.9); color: white; }
        #mode-place { background: rgba(39, 174, 96, 0.9); color: white; }
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="crosshair">+</div>
    <div id="mode-indicator"></div>

    <script>
        let mapMode = "normal";

        const map = new maplibregl.Map({
            container: 'map',
            style: 'https://tiles.openfreemap.org/styles/liberty',
            center: [28.8638, 47.0101],
            zoom: 16,
            pitch: 60,
            bearing: 45,
            antialias: true
        });

        map.addControl(new maplibregl.NavigationControl({visualizePitch: true, showZoom: true, showCompass: true}));

        map.on('load', () => {
            map.addSource('coverage-source', {
                type: 'geojson',
                data: { type: 'FeatureCollection', features: [] }
            });

            map.addLayer({
                id: 'coverage-fill',
                type: 'fill',
                source: 'coverage-source',
                paint: { 'fill-color': ['get', 'fill'], 'fill-opacity': 0.75 }
            });

            map.addLayer({
                id: 'coverage-outline',
                type: 'line',
                source: 'coverage-source',
                paint: { 'line-color': ['get', 'stroke'], 'line-width': 1.5, 'line-opacity': 0.8 }
            });

            map.addSource('openfreemap-buildings', {
                type: 'vector',
                url: 'https://tiles.openfreemap.org/planet.json'
            });

            map.addLayer({
                'id': '3d-buildings-extrusion',
                'source': 'openfreemap-buildings',
                'source-layer': 'building',
                'type': 'fill-extrusion',
                'minzoom': 13,
                'paint': {
                    'fill-extrusion-color': '#c5ccd6',
                    'fill-extrusion-height': ['*', ['coalesce', ['get', 'render_height'], ['get', 'height'], 15], 1.2],
                    'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], ['get', 'min_height'], 0],
                    'fill-extrusion-opacity': 0.85
                }
            });

            window.deckOverlay = new deck.MapboxOverlay({ interleaved: true, layers: [] });
            map.addControl(window.deckOverlay);
        });

        window.setMapMode = function(mode) {
            mapMode = mode;
            const indicator = document.getElementById('mode-indicator');
            if (mode === 'select') {
                indicator.textContent = 'Режим выбора — кликните на вышку';
                indicator.style.display = 'block';
                indicator.id = 'mode-indicator';
                indicator.className = '';
                indicator.style.background = 'rgba(41, 128, 185, 0.9)';
                indicator.style.color = 'white';
                map.getCanvas().style.cursor = 'crosshair';
            } else if (mode === 'place') {
                indicator.textContent = 'Режим размещения — кликните на карту';
                indicator.style.display = 'block';
                indicator.style.background = 'rgba(39, 174, 96, 0.9)';
                indicator.style.color = 'white';
                map.getCanvas().style.cursor = 'cell';
            } else {
                indicator.style.display = 'none';
                map.getCanvas().style.cursor = '';
            }
        };

        map.on('click', (e) => {
            const lat = e.lngLat.lat;
            const lon = e.lngLat.lng;
            if (mapMode === 'place') {
                console.log(`ADD_TOWER:${lat}:${lon}`);
            } else if (mapMode === 'select') {
                console.log(`SELECT_TOWER:${lat}:${lon}`);
            }
        });

        let hoverThrottle = 0;
        map.on('mousemove', (e) => {
            const now = Date.now();
            if (now - hoverThrottle > 1000) {
                hoverThrottle = now;
                console.log(`HOVER_LOAD:${e.lngLat.lat}:${e.lngLat.lng}`);
            }
        });

        function getAngleOffset(angle, azimuth) {
            return (angle - azimuth + 540) % 360 - 180;
        }

        function buildCoveragePolygons(t) {
            const features = [];
            if (!t.ray_results || t.ray_results.length === 0) return features;

            const zoneConfigs = [
                { name: 'weak',      fill: 'rgba(180, 40, 40, 0.40)', stroke: 'rgba(180, 40, 40, 0.85)' },
                { name: 'good',      fill: 'rgba(231, 76, 60, 0.50)', stroke: 'rgba(231, 76, 60, 0.90)' },
                { name: 'excellent', fill: 'rgba(241, 196, 15, 0.60)', stroke: 'rgba(241, 196, 15, 0.95)' },
                { name: 'green',     fill: 'rgba(46, 204, 113, 0.70)', stroke: 'rgba(46, 204, 113, 1.00)' }
            ];

            const beamwidth = t.beamwidth !== undefined ? t.beamwidth : 360.0;
            const azimuth = t.azimuth !== undefined ? t.azimuth : 0.0;
            const isSector = beamwidth < 360.0;
            const center = [t.lon, t.lat];

            for (const cfg of zoneConfigs) {
                const zonePoints = t.ray_results.filter(r => r.zone === cfg.name);
                zonePoints.sort((a, b) => getAngleOffset(a.angle, azimuth) - getAngleOffset(b.angle, azimuth));
                if (zonePoints.length === 0) continue;

                const ring = [];
                if (isSector) {
                    ring.push(center);
                    for (const r of zonePoints) ring.push([r.lon, r.lat]);
                    ring.push(center);
                } else {
                    for (const r of zonePoints) ring.push([r.lon, r.lat]);
                    if (ring.length > 0) ring.push([ring[0][0], ring[0][1]]);
                }

                if (ring.length >= 3) {
                    features.push({
                        type: 'Feature',
                        geometry: { type: 'Polygon', coordinates: [ring] },
                        properties: { fill: cfg.fill, stroke: cfg.stroke, zone: cfg.name }
                    });
                }
            }
            return features;
        }

        window.updateTowers = function(towersData) {
            if (!window.deckOverlay) return;

            const allFeatures = [];
            towersData.forEach(t => {
                if (t.ray_results && t.ray_results.length > 0) {
                    allFeatures.push(...buildCoveragePolygons(t));
                }
            });

            if (map.getSource('coverage-source')) {
                map.getSource('coverage-source').setData({ type: 'FeatureCollection', features: allFeatures });
            }

            const layers = [];
            const standardTowers = towersData.filter(t => !t.model_b64);
            const modelTowers = towersData.filter(t => t.model_b64);

            if (standardTowers.length > 0) {
                layers.push(new deck.ColumnLayer({
                    id: 'tower-columns-default',
                    data: standardTowers,
                    getPosition: d => [d.lon, d.lat],
                    getElevation: d => d.height,
                    radius: 4,
                    getFillColor: [30, 30, 30, 255],
                    extruded: true,
                    pickable: true
                }));
            }

            if (modelTowers.length > 0) {
                layers.push(new deck.ScenegraphLayer({
                    id: 'tower-models-custom',
                    data: modelTowers,
                    pickable: true,
                    scenegraph: d => d.model_b64,
                    getPosition: d => [d.lon, d.lat],
                    getOrientation: d => [0, 0, d.tilt],
                    sizeScale: 1,
                    getSize: d => d.scale
                }));
            }

            window.deckOverlay.setProps({ layers: layers });
        };
    </script>
</body>
</html>
"""
        self.web_view.setHtml(html_code, QUrl("http://localhost"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CellTower3DApp()
    window.show()
    sys.exit(app.exec())