"""Диалоги и вспомогательные виджеты интерфейса."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
                             QVBoxLayout, QWidget)

import geo
from calculations import ZONE_LABELS_RU, eirp_dbm, quality_of

QUALITY_COLORS = {"green": "#2ecc71", "excellent": "#a3d93b", "good": "#f1c40f",
                  "weak": "#e76432", "none": "#8a93a0"}


def _spin(lo, hi, val, decimals=1, suffix="", step=None):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    if step:
        s.setSingleStep(step)
    return s


class ParamsForm(QWidget):
    """Радиопараметры вышки (общая часть диалогов создания типа и редактирования)."""

    def __init__(self, color_map, parent=None):
        super().__init__(parent)
        self._colors = dict(color_map)
        lay = QFormLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.freq = _spin(100, 6000, 1800, 0, " МГц")
        self.power = _spin(0.1, 500, 10, 1, " Вт")
        self.height = _spin(1, 300, 5, 1, " м")
        self.tilt = _spin(0, 90, 6, 1, "°")
        self.wall_loss = _spin(0, 50, 20, 1, " дБ")
        self.azimuth = _spin(0, 360, 0, 0, "°")
        self.beamwidth = _spin(5, 360, 360, 0, "°")
        self.color = QComboBox()
        self.color.addItems(list(self._colors.keys()))
        lay.addRow("Частота:", self.freq)
        lay.addRow("Мощность:", self.power)
        lay.addRow("Высота подвеса:", self.height)
        lay.addRow("Наклон (Tilt):", self.tilt)
        lay.addRow("Потери на стену:", self.wall_loss)
        lay.addRow("Азимут:", self.azimuth)
        lay.addRow("Ширина луча:", self.beamwidth)
        lay.addRow("Цвет:", self.color)
        self.eirp_label = QLabel()
        self.eirp_label.setProperty("role", "dim")
        lay.addRow("", self.eirp_label)
        for w in (self.power, self.beamwidth):
            w.valueChanged.connect(self._update_eirp)
        self._update_eirp()

    def _update_eirp(self):
        self.eirp_label.setText(f"EIRP ≈ {eirp_dbm(self.power.value(), self.beamwidth.value()):.1f} дБм")

    def set_values(self, d):
        self.freq.setValue(d.get("freq", 1800))
        self.power.setValue(d.get("power", 10))
        self.height.setValue(d.get("height", 5))
        self.tilt.setValue(d.get("tilt", 6))
        self.wall_loss.setValue(d.get("wall_loss", 20))
        self.azimuth.setValue(d.get("azimuth", 0))
        self.beamwidth.setValue(d.get("beamwidth", 360))
        col = list(d.get("color", [255, 0, 0, 255]))
        for name, rgba in self._colors.items():
            if list(rgba) == col:
                self.color.setCurrentText(name)
                return
        self._colors["Текущий"] = col                     # цвет вне палитры — сохраняем как есть
        if self.color.findText("Текущий") < 0:
            self.color.addItem("Текущий")
        self.color.setCurrentText("Текущий")

    def values(self):
        return {"freq": self.freq.value(), "power": self.power.value(), "height": self.height.value(),
                "tilt": self.tilt.value(), "wall_loss": self.wall_loss.value(),
                "azimuth": self.azimuth.value(), "beamwidth": self.beamwidth.value(),
                "color": list(self._colors[self.color.currentText()]),
                "color_name": self.color.currentText()}


class CreateTypeDialog(QDialog):
    def __init__(self, color_map, preset=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать тип вышки")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Новый тип")
        top.addRow("Название типа:", self.name_input)
        lay.addLayout(top)
        self.form = ParamsForm(color_map)
        lay.addWidget(self.form)
        if preset:
            self.name_input.setText(preset.get("name", ""))
            self.form.set_values(preset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def get_data(self):
        d = self.form.values()
        d["name"] = self.name_input.text().strip()
        return d


class EditTowerDialog(QDialog):
    """Полное редактирование вышки: имя, координаты и все радиопараметры."""

    def __init__(self, tower_types, tower, color_map, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактировать вышку")
        self.setMinimumWidth(420)
        self._types = tower_types
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name_input = QLineEdit(tower.name)
        top.addRow("Имя:", self.name_input)
        self.type_combo = QComboBox()
        self.type_combo.addItem("— свои параметры —")
        self.type_combo.addItems(list(tower_types.keys()))
        top.addRow("Тип:", self.type_combo)
        self.lat_input = _spin(-90, 90, tower.lat, 6, "°")
        self.lon_input = _spin(-180, 180, tower.lon, 6, "°")
        top.addRow("Широта:", self.lat_input)
        top.addRow("Долгота:", self.lon_input)
        lay.addLayout(top)
        self.form = ParamsForm(color_map)
        self.form.set_values({**tower.params(), "color": tower.color})
        lay.addWidget(self.form)
        self.type_combo.currentTextChanged.connect(self._apply_type)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        if tower.type_name in tower_types:
            self.type_combo.setCurrentText(tower.type_name)
            self.form.set_values({**tower.params(), "color": tower.color})   # не затирать правки вышки

    def _apply_type(self, name):
        if name in self._types:
            self.form.set_values(self._types[name])

    def get_data(self):
        d = self.form.values()
        t = self.type_combo.currentText()
        d.update({"name": self.name_input.text().strip(), "lat": self.lat_input.value(),
                  "lon": self.lon_input.value(), "type_name": t if t in self._types else None})
        return d


# --------------------------------------------------------------------------- #
#  Профиль трассы
# --------------------------------------------------------------------------- #
class LinkProfileWidget(QWidget):
    def __init__(self, res, theme, parent=None):
        super().__init__(parent)
        self.res, self.theme = res, theme
        self.setMinimumSize(640, 280)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        p.fillRect(self.rect(), QColor(t["bg1"]))
        prof, D = self.res["profile"], self.res["distance_m"]
        ml, mr, mt, mb = 52, 16, 14, 34
        area = QRectF(ml, mt, self.width() - ml - mr, self.height() - mt - mb)
        top = max(float(prof["heights"].max()) if prof["heights"].size else 0.0, prof["tx_h"], 10.0) * 1.2

        def X(d):
            return area.left() + area.width() * d / max(D, 1e-6)

        def Y(h):
            return area.bottom() - area.height() * max(h, 0.0) / top

        # сетка и подписи осей
        p.setPen(QPen(QColor(t["border"]), 1))
        font = p.font()
        font.setPointSizeF(8.5)
        p.setFont(font)
        for i in range(5):
            h = top * i / 4
            p.drawLine(QPointF(area.left(), Y(h)), QPointF(area.right(), Y(h)))
            p.setPen(QColor(t["dim"]))
            p.drawText(QRectF(0, Y(h) - 8, ml - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{h:.0f} м")
            p.setPen(QPen(QColor(t["border"]), 1))
        p.setPen(QColor(t["dim"]))
        for frac, al in ((0.0, Qt.AlignmentFlag.AlignLeft), (0.5, Qt.AlignmentFlag.AlignHCenter),
                         (1.0, Qt.AlignmentFlag.AlignRight)):
            x = area.left() + area.width() * frac
            p.drawText(QRectF(x - 50, area.bottom() + 6, 100, 18), al | Qt.AlignmentFlag.AlignTop,
                       geo.format_distance(D * frac))

        # зона Френеля
        d, los, fr = prof["dist"], prof["los_h"], prof["fresnel_r"]
        if d.size:
            up = [QPointF(X(a), Y(h + r)) for a, h, r in zip(d, los, fr)]
            low = [QPointF(X(a), Y(h - r)) for a, h, r in zip(d, los, fr)]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(62, 139, 255, 45)))
            p.drawPolygon(QPolygonF(up + low[::-1]))

        # здания: красные — учитываются моделью (не ниже антенны), серые — ниже антенны
        if d.size:
            step_px = X(d[0]) - X(0) if d.size else 1
            i, n = 0, d.size
            heights, hit = prof["heights"], prof["hit"]
            while i < n:
                if heights[i] <= 0:
                    i += 1
                    continue
                j = i
                while j + 1 < n and heights[j + 1] == heights[i] and hit[j + 1] == hit[i]:
                    j += 1
                col = QColor(231, 100, 80, 200) if hit[i] else QColor(140, 150, 165, 170)
                p.setBrush(QBrush(col))
                x0, x1 = X(d[i]) - step_px, X(d[j])
                p.drawRect(QRectF(x0, Y(heights[i]), max(x1 - x0, 1.0), Y(0) - Y(heights[i])))
                i = j + 1

        # прямая видимость и мачты
        p.setPen(QPen(QColor("#ffcc00") if self.res["los_clear"] else QColor("#ff6b6b"), 2, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(X(0), Y(prof["tx_h"])), QPointF(X(D), Y(prof["rx_h"])))
        p.setPen(QPen(QColor(t["text"]), 3))
        p.drawLine(QPointF(X(0), Y(0)), QPointF(X(0), Y(prof["tx_h"])))
        p.setBrush(QBrush(QColor(t["accent"])))
        p.drawEllipse(QPointF(X(D), Y(prof["rx_h"])), 5, 5)
        p.end()


class LinkProfileDialog(QDialog):
    def __init__(self, tower_name, res, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Профиль трассы: {tower_name} → точка")
        self.resize(780, 520)
        lay = QVBoxLayout(self)
        info = QTextBrowser()
        info.setMaximumHeight(150)
        q = res["quality"]
        rssi = "вне сектора антенны" if res["rssi_dbm"] is None else f"{res['rssi_dbm']:.1f} дБм"
        los = "свободна" if res["los_clear"] else "затенена зданиями"
        total_txt = "—" if res["rssi_dbm"] is None else f"{res['rssi_dbm']:.1f} дБм"
        info.setHtml(
            f"<b>Уровень сигнала:</b> <span style='color:{QUALITY_COLORS[q]}'><b>{rssi}</b></span>"
            f" ({ZONE_LABELS_RU.get(q, 'нет покрытия')})<br>"
            f"Расстояние: {geo.format_distance(res['distance_m'])}, азимут {geo.format_bearing(res['azimuth'])}<br>"
            f"EIRP {res['eirp_dbm']:.1f} дБм − потери на трассе {res['path_loss_db']:.1f} дБ"
            f" − стены {res['wall_loss_db']:.1f} дБ − дифракция {res.get('diffraction_db', 0.0):.1f} дБ"
            + (f" − диаграмма антенны {res['pattern_db']:.1f} дБ" if res.get('pattern_db') else "")
            + f" = {total_txt}<br>"
            f"Пересечено стен: {res['walls']}, путь внутри зданий: {res['inside_m']:.0f} м<br>"
            f"Геометрическая прямая видимость: {los}; запас по 1-й зоне Френеля: "
            f"{res['fresnel_clearance'] * 100:.0f}% радиуса"
        )
        lay.addWidget(info)
        lay.addWidget(LinkProfileWidget(res, theme), stretch=1)
        legend = QLabel("<span style='color:#e76432'>■</span> здание учитывается моделью (не ниже антенны) &nbsp; "
                        "<span style='color:#8c96a5'>■</span> ниже антенны — дифракция, если заходит в зону Френеля &nbsp; "
                        "<span style='color:#3e8bff'>■</span> зона Френеля")
        legend.setProperty("role", "dim")
        lay.addWidget(legend)
        btn = QPushButton("Закрыть")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignRight)


# --------------------------------------------------------------------------- #
#  Радиокалькулятор
# --------------------------------------------------------------------------- #
class CalculatorDialog(QDialog):
    def __init__(self, default_freq=1800.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Радиокалькулятор")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)

        g1 = QGroupBox("Мощность: дБм ↔ Вт")
        f1 = QFormLayout(g1)
        self.dbm_in = _spin(-150, 90, 43, 2, " дБм")
        self.dbm_out = QLabel()
        self.w_in = _spin(0.000001, 100000, 20, 6, " Вт")
        self.w_out = QLabel()
        f1.addRow("Мощность:", self.dbm_in)
        f1.addRow("= ", self.dbm_out)
        f1.addRow("Мощность:", self.w_in)
        f1.addRow("= ", self.w_out)
        lay.addWidget(g1)

        g2 = QGroupBox("Свободное пространство и зона Френеля")
        f2 = QFormLayout(g2)
        self.f_in = _spin(100, 100000, default_freq, 0, " МГц")
        self.d_in = _spin(0.001, 10000, 1.0, 3, " км")
        self.fspl_out = QLabel()
        self.lambda_out = QLabel()
        self.fres_out = QLabel()
        f2.addRow("Частота:", self.f_in)
        f2.addRow("Расстояние:", self.d_in)
        f2.addRow("Потери FSPL:", self.fspl_out)
        f2.addRow("Длина волны:", self.lambda_out)
        f2.addRow("1-я зона Френеля (середина):", self.fres_out)
        lay.addWidget(g2)

        g3 = QGroupBox("Бюджет радиолинии")
        f3 = QFormLayout(g3)
        self.ptx = _spin(-30, 90, 40, 1, " дБм")
        self.gtx = _spin(-10, 50, 14, 1, " дБи")
        self.grx = _spin(-10, 50, 0, 1, " дБи")
        self.lcable = _spin(0, 30, 2, 1, " дБ")
        self.lpath = _spin(0, 250, 110, 1, " дБ")
        self.prx_out = QLabel()
        f3.addRow("Мощность передатчика:", self.ptx)
        f3.addRow("Усиление передающей антенны:", self.gtx)
        f3.addRow("Усиление приёмной антенны:", self.grx)
        f3.addRow("Потери в фидере:", self.lcable)
        f3.addRow("Потери на трассе:", self.lpath)
        f3.addRow("Уровень на входе приёмника:", self.prx_out)
        lay.addWidget(g3)

        for w in (self.dbm_in, self.w_in, self.f_in, self.d_in, self.ptx, self.gtx, self.grx,
                  self.lcable, self.lpath):
            w.valueChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        self.dbm_out.setText(f"{geo.dbm_to_w(self.dbm_in.value()):.6g} Вт  ({geo.dbm_to_w(self.dbm_in.value()) * 1000:.6g} мВт)")
        self.w_out.setText(f"{geo.w_to_dbm(self.w_in.value()):.2f} дБм")
        f, d_km = self.f_in.value(), self.d_in.value()
        lam = 300.0 / f
        self.fspl_out.setText(f"{geo.fspl_db(d_km * 1000, f):.2f} дБ")
        self.lambda_out.setText(f"{lam:.3f} м")
        self.fres_out.setText(f"{math.sqrt(lam * d_km * 1000.0 / 4.0) :.1f} м")
        prx = self.ptx.value() + self.gtx.value() + self.grx.value() - self.lcable.value() - self.lpath.value()
        q = quality_of(prx)
        self.prx_out.setText(f"<b>{prx:.1f} дБм</b> — <span style='color:{QUALITY_COLORS[q]}'>"
                             f"{ZONE_LABELS_RU.get(q, 'нет покрытия')}</span>")
