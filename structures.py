"""Модуль опорных конструкций вышек (каркасов, мачт, монополей и опор).

Реализует концепцию:
- Антенна сама по себе не имеет высоты от земли.
- Антенна монтируется либо на здание (высота крыши + вынос),
  либо на опорную конструкцию / каркас вышки (мачту, решётчатую башню, монополь).
- Несколько секторных антенн могут крепиться к одному каркасу на разной высоте и азимутах.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)


@dataclass
class StructureTypeSpec:
    id: str
    name: str
    kind: str             # lattice, guyed, monopole, pole, roof_mast
    height: float
    description: str


BUILTIN_STRUCTURE_SPECS: list[StructureTypeSpec] = [
    StructureTypeSpec("lattice_30", "Решётчатая башня 30 м", "lattice", 30.0, "Стальная четырёхгранная решётчатая опора"),
    StructureTypeSpec("lattice_40", "Решётчатая башня 40 м", "lattice", 40.0, "Стандартная макро-башня для размещения до 6 секторов"),
    StructureTypeSpec("lattice_55", "Решётчатая башня 55 м", "lattice", 55.0, "Высокая решётчатая башня для пригородных и узловых позиций"),
    StructureTypeSpec("lattice_70", "Решётчатая башня 70 м", "lattice", 70.0, "Тяжёлая решётчатая опора для дальнего радиопокрытия и РРЛ"),
    StructureTypeSpec("guyed_30", "Мачта на растяжках 30 м", "guyed", 30.0, "Трёхгранная мачтовая конструкция на оттяжках"),
    StructureTypeSpec("guyed_45", "Мачта на растяжках 45 м", "guyed", 45.0, "Мачта на трёхъярусных стальных оттяжках"),
    StructureTypeSpec("monopole_25", "Монополь (трубчатая опора) 25 м", "monopole", 25.0, "Эстетичная коническая стальная опора для городской среды"),
    StructureTypeSpec("monopole_35", "Монополь 35 м", "monopole", 35.0, "Городской монополь с круговой площадкой обслуживания"),
    StructureTypeSpec("pole_15", "Трубостойка / Столб 15 м", "pole", 15.0, "Облегчённая опора для микросотовых секторов и Small Cell"),
    StructureTypeSpec("roof_mast_6", "Крышная трубостойка 6 м", "roof_mast", 6.0, "Металлическая конструкция для монтажа на плоских кровлях"),
]


@dataclass
class TowerStructure:
    id: str
    name: str
    lat: float
    lon: float
    model_id: str
    height: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TowerStructure:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", "Каркас")),
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            model_id=str(d.get("model_id", "lattice_40")),
            height=float(d.get("height", 40.0)),
        )


def init_structures_db(conn: sqlite3.Connection):
    """Инициализация таблицы каркасов вышек."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tower_structures (
                id TEXT PRIMARY KEY,
                name TEXT,
                lat REAL,
                lon REAL,
                model_id TEXT,
                height REAL
            )
        """)


def load_all_structures(conn: sqlite3.Connection) -> dict[str, TowerStructure]:
    init_structures_db(conn)
    rows = conn.execute("SELECT * FROM tower_structures ORDER BY id").fetchall()
    res = {}
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "name": r[1], "lat": r[2], "lon": r[3], "model_id": r[4], "height": r[5]
        }
        res[d["id"]] = TowerStructure.from_dict(d)
    return res


def save_structure(conn: sqlite3.Connection, s: TowerStructure):
    init_structures_db(conn)
    with conn:
        conn.execute("""
            INSERT INTO tower_structures (id, name, lat, lon, model_id, height)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, lat=excluded.lat, lon=excluded.lon,
                model_id=excluded.model_id, height=excluded.height
        """, (s.id, s.name, s.lat, s.lon, s.model_id, s.height))


def delete_structure(conn: sqlite3.Connection, sid: str):
    init_structures_db(conn)
    with conn:
        conn.execute("DELETE FROM tower_structures WHERE id = ?", (sid,))


class CreateStructureDialog(QDialog):
    """Диалог создания нового каркаса / мачты вышки."""

    def __init__(self, lat: float, lon: float, existing_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать каркас вышки (опорную конструкцию)")
        self.resize(440, 260)
        self.lat = lat
        self.lon = lon
        self.structure: Optional[TowerStructure] = None

        lay = QVBoxLayout(self)
        form = QFormLayout()

        self.ed_name = QLineEdit(f"Каркас #{existing_count + 1}")
        form.addRow("Название каркаса:", self.ed_name)

        self.cmb_model = QComboBox()
        for spec in BUILTIN_STRUCTURE_SPECS:
            self.cmb_model.addItem(f"{spec.name} ({spec.height:.0f} м)", spec)
        self.cmb_model.setCurrentIndex(1)  # lattice_40 по умолчанию
        self.cmb_model.currentIndexChanged.connect(self._on_model_change)
        form.addRow("Тип конструкции:", self.cmb_model)

        self.spn_height = QDoubleSpinBox()
        self.spn_height.setRange(3.0, 150.0)
        self.spn_height.setValue(40.0)
        self.spn_height.setSuffix(" м")
        form.addRow("Высота каркаса:", self.spn_height)

        self.lbl_coords = QLabel(f"{lat:.6f}, {lon:.6f}")
        form.addRow("Координаты:", self.lbl_coords)

        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_model_change(self):
        spec = self.cmb_model.currentData()
        if spec:
            self.spn_height.setValue(spec.height)

    def _on_ok(self):
        spec = self.cmb_model.currentData()
        sid = f"S_{int(self.lat * 100000)}_{int(self.lon * 100000)}"
        self.structure = TowerStructure(
            id=sid,
            name=self.ed_name.text().strip() or "Каркас вышки",
            lat=self.lat,
            lon=self.lon,
            model_id=spec.id if spec else "lattice_40",
            height=self.spn_height.value(),
        )
        self.accept()


class MountManagerDialog(QDialog):
    """Диалог управления креплением антенны (здание или каркас)."""

    def __init__(self, current_lat: float, current_lon: float,
                 structures: dict[str, TowerStructure],
                 building_roof_h: float = 0.0,
                 current_mount_type: str = "ground",
                 current_mount_id: Optional[str] = None,
                 current_offset: float = 0.0,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Монтаж антенны (прикрепление к зданию / каркасу)")
        self.resize(500, 360)

        self.lat = current_lat
        self.lon = current_lon
        self.structures = structures
        self.building_roof_h = building_roof_h

        self.result_mount_type = current_mount_type
        self.result_mount_id = current_mount_id
        self.result_offset = current_offset
        self.result_total_height = 0.0

        lay = QVBoxLayout(self)

        # Выбор способа монтажа
        grp = QGroupBox("Способ размещения антенны")
        g_lay = QVBoxLayout(grp)

        # 1. Здание
        self.rb_building = QRadioButton(f"Кровля здания (определено: {building_roof_h:.1f} м)")
        if building_roof_h <= 0.0:
            self.rb_building.setText("Кровля здания (здание в данной точке не обнаружено, 0 м)")
        g_lay.addWidget(self.rb_building)

        h_bld_box = QHBoxLayout()
        h_bld_box.setContentsMargins(20, 0, 0, 0)
        h_bld_box.addWidget(QLabel("Высота стойки над крышей:"))
        self.spn_bld_offset = QDoubleSpinBox()
        self.spn_bld_offset.setRange(0.0, 30.0)
        self.spn_bld_offset.setValue(max(0.0, current_offset if current_mount_type == "building" else 3.0))
        self.spn_bld_offset.setSuffix(" м")
        self.spn_bld_offset.valueChanged.connect(self._recalc)
        h_bld_box.addWidget(self.spn_bld_offset)
        g_lay.addLayout(h_bld_box)

        # 2. Каркас вышки
        self.rb_struct = QRadioButton("Каркас вышки / мачта (опорная башня)")
        g_lay.addWidget(self.rb_struct)

        h_str_box = QHBoxLayout()
        h_str_box.setContentsMargins(20, 0, 0, 0)
        h_str_box.addWidget(QLabel("Выбрать каркас:"))
        self.cmb_struct = QComboBox()
        for sid, s in self.structures.items():
            dist = ((s.lat - self.lat) ** 2 + (s.lon - self.lon) ** 2) ** 0.5 * 111320
            self.cmb_struct.addItem(f"{s.name} ({s.height:.0f} м) — {dist:.0f} м", s)
        h_str_box.addWidget(self.cmb_struct, stretch=1)

        self.btn_new_struct = QPushButton("+ Новый каркас")
        self.btn_new_struct.clicked.connect(self._create_new_struct)
        h_str_box.addWidget(self.btn_new_struct)
        g_lay.addLayout(h_str_box)

        h_sus_box = QHBoxLayout()
        h_sus_box.setContentsMargins(20, 0, 0, 0)
        h_sus_box.addWidget(QLabel("Высота подвеса на каркасе:"))
        self.spn_str_h = QDoubleSpinBox()
        self.spn_str_h.setRange(1.0, 150.0)
        self.spn_str_h.setValue(35.0)
        self.spn_str_h.setSuffix(" м")
        self.spn_str_h.valueChanged.connect(self._recalc)
        h_sus_box.addWidget(self.spn_str_h)
        g_lay.addLayout(h_sus_box)

        # 3. Наземная / без крепления
        self.rb_ground = QRadioButton("Не закреплена / на земле (высота 0 м)")
        g_lay.addWidget(self.rb_ground)

        lay.addWidget(grp)

        # Переключение радиокнопок
        self.rb_building.toggled.connect(self._on_type_toggle)
        self.rb_struct.toggled.connect(self._on_type_toggle)
        self.rb_ground.toggled.connect(self._on_type_toggle)

        # Итоговая высота
        self.lbl_result = QLabel("<b>Результирующая высота антенны: 0.0 м</b>")
        self.lbl_result.setStyleSheet("font-size: 13px; color: #3e8bff; padding: 6px;")
        lay.addWidget(self.lbl_result)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        # Инициализация выбора
        if current_mount_type == "building" or (current_mount_type == "ground" and building_roof_h > 0):
            self.rb_building.setChecked(True)
        elif current_mount_type == "structure" and self.structures:
            self.rb_struct.setChecked(True)
            if current_mount_id in self.structures:
                s = self.structures[current_mount_id]
                idx = self.cmb_struct.findData(s)
                if idx >= 0:
                    self.cmb_struct.setCurrentIndex(idx)
                self.spn_str_h.setValue(max(1.0, s.height - current_offset))
        else:
            if building_roof_h > 0:
                self.rb_building.setChecked(True)
            elif self.structures:
                self.rb_struct.setChecked(True)
            else:
                self.rb_ground.setChecked(True)

        self._recalc()

    def _create_new_struct(self):
        dlg = CreateStructureDialog(self.lat, self.lon, len(self.structures), self)
        if dlg.exec() and dlg.structure:
            s = dlg.structure
            self.structures[s.id] = s
            self.cmb_struct.addItem(f"{s.name} ({s.height:.0f} м) — 0 м", s)
            self.cmb_struct.setCurrentIndex(self.cmb_struct.count() - 1)
            self.rb_struct.setChecked(True)
            self.spn_str_h.setValue(s.height)
            self._recalc()

    def _on_type_toggle(self):
        self.spn_bld_offset.setEnabled(self.rb_building.isChecked())
        self.cmb_struct.setEnabled(self.rb_struct.isChecked())
        self.btn_new_struct.setEnabled(self.rb_struct.isChecked())
        self.spn_str_h.setEnabled(self.rb_struct.isChecked())
        self._recalc()

    def _recalc(self):
        if self.rb_building.isChecked():
            h = max(1.0, self.building_roof_h + self.spn_bld_offset.value())
            self.lbl_result.setText(f"<b>Результирующая высота: {h:.1f} м</b> (крыша {self.building_roof_h:.1f} м + стойка {self.spn_bld_offset.value():.1f} м)")
            self.result_total_height = h
        elif self.rb_struct.isChecked():
            s = self.cmb_struct.currentData()
            max_h = s.height if s else 40.0
            self.spn_str_h.setMaximum(max_h)
            h = min(self.spn_str_h.value(), max_h)
            self.lbl_result.setText(f"<b>Результирующая высота: {h:.1f} м</b> (подвес на «{s.name if s else 'каркасе'}» высотой {max_h:.0f} м)")
            self.result_total_height = h
        else:
            self.lbl_result.setText("<b>Результирующая высота: 0.0 м</b> (наземное размещение без подвеса)")
            self.result_total_height = 0.0

    def _on_ok(self):
        if self.rb_building.isChecked():
            self.result_mount_type = "building"
            self.result_mount_id = None
            self.result_offset = self.spn_bld_offset.value()
        elif self.rb_struct.isChecked():
            s = self.cmb_struct.currentData()
            self.result_mount_type = "structure"
            self.result_mount_id = s.id if s else None
            self.result_offset = (s.height - self.spn_str_h.value()) if s else 0.0
        else:
            self.result_mount_type = "ground"
            self.result_mount_id = None
            self.result_offset = 0.0
        self.accept()
