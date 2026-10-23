"""Интерфейс сетевой части: палитры устройств и кабелей, настройка, ping (PDU), тест нагрузки."""
import csv
import io
import ipaddress
import json
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget, QAbstractItemView, QApplication, QHeaderView)

import geo
import loadtest
import netcatalog as NC
import snmp
from dialogs import QUALITY_COLORS
from network import Network, Link, fmt_speed

ROLE_PREFIX = {"switch_l2": "SW", "switch_l3": "L3", "router": "R", "firewall": "FW", "radio": "MW", "core": "CORE"}
STATE_TEXT = {"up": ("работает", "#2ecc71"), "warn": ("с ограничением", "#f5a524"), "down": ("не работает", "#e5484d")}
UTIL_COLORS = ((0.5, "#2ecc71"), (0.8, "#f1c40f"), (0.95, "#e76432"), (9.0, "#e5484d"))


def util_color(u):
    return next(c for lim, c in UTIL_COLORS if u < lim)


def _fmt_mbps(v):
    return f"{v / 1000:.2f} Гбит/с" if v >= 1000 else f"{v:.1f} Мбит/с"


# --------------------------------------------------------------------------- #
#  Диалоги
# --------------------------------------------------------------------------- #
class DeviceDialog(QDialog):
    """Настройка устройства: питание, радиопараметры БС, IP на портах и управление, шлюз, SNMP-агент."""

    def __init__(self, net, dev, parent=None):
        super().__init__(parent)
        self.net, self.dev = net, dev
        self.setWindowTitle(f"Настройка устройства: {dev.name}")
        self.setMinimumWidth(580)
        lay = QVBoxLayout(self)

        tabs = QTabWidget()

        # --- Вкладка 1: Основные ---
        p_main = QWidget()
        lay_main = QVBoxLayout(p_main)
        form = QFormLayout()
        self.name = QLineEdit(dev.name)
        self.name.setEnabled(dev.tower_id is None)
        form.addRow("Имя:", self.name)
        form.addRow("Модель:", QLabel(dev.spec["name"]))
        self.power = QComboBox()
        self.power.addItems(["Местное питание (AC/DC)", "PoE по кабелю (нужен PSE-порт коммутатора)"])
        self.power.setCurrentIndex(1 if dev.power_mode == "poe" else 0)
        has_pd = any(p.pd for p in dev.ports())
        self.power.setEnabled(has_pd)
        form.addRow("Питание:", self.power)
        self.mount = QDoubleSpinBox()
        self.mount.setRange(1, 300)
        self.mount.setSuffix(" м")
        self.mount.setValue(dev.mount_h)
        self.mount.setEnabled(dev.role == "radio")
        form.addRow("Высота подвеса антенны:", self.mount)
        lay_main.addLayout(form)

        self.radio_box = None
        if dev.role == "tower":
            self.radio_box = QGroupBox("Радиоинтерфейс БС (для теста нагрузки)")
            rf = QFormLayout(self.radio_box)
            self.tech = QComboBox()
            self.tech.addItems(["LTE", "NR"])
            self.tech.setCurrentText(dev.radio.get("tech", "LTE"))
            self.bw = QDoubleSpinBox()
            self.bw.setRange(1.4, 400)
            self.bw.setSuffix(" МГц")
            self.bw.setValue(dev.radio.get("bw_mhz", 20.0))
            self.layers = QSpinBox()
            self.layers.setRange(1, 16)
            self.layers.setValue(int(dev.radio.get("layers", 2)))
            self.max_users = QSpinBox()
            self.max_users.setRange(1, 5000)
            self.max_users.setValue(int(dev.radio.get("max_users", 300)))
            rf.addRow("Технология:", self.tech)
            rf.addRow("Полоса:", self.bw)
            rf.addRow("Слоёв MIMO:", self.layers)
            rf.addRow("Макс. активных абонентов:", self.max_users)
            lay_main.addWidget(self.radio_box)
        lay_main.addStretch(1)
        tabs.addTab(p_main, "Параметры")

        # --- Вкладка 2: IP-адреса и маршруты ---
        p_ip = QWidget()
        lay_ip = QVBoxLayout(p_ip)
        lay_ip.addWidget(QLabel("IP-адреса на портах (формат 10.0.0.1/24):"))
        ports = [p for p in dev.ports() if net.port_link(dev.id, p.name) or p.name in dev.ips]
        self.ip_ports = [p.name for p in ports]
        self.ip_table = QTableWidget(len(ports), 2)
        self.ip_table.setHorizontalHeaderLabels(["Порт", "IP/маска"])
        self.ip_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.ip_table.verticalHeader().setVisible(False)
        self.ip_table.setMaximumHeight(150)
        for r, p in enumerate(ports):
            item = QTableWidgetItem(f"{p.name}  ({p.kind} {fmt_speed(p.mbps)})")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.ip_table.setItem(r, 0, item)
            self.ip_table.setItem(r, 1, QTableWidgetItem(dev.ips.get(p.name, "")))
        lay_ip.addWidget(self.ip_table)

        f_mgmt = QFormLayout()
        self.mgmt_ip = QLineEdit(dev.ips.get("mgmt", ""))
        self.mgmt_ip.setPlaceholderText("напр. 192.168.1.10/24 (для L2 коммутаторов / вышки)")
        f_mgmt.addRow("Управляющий IP (Management):", self.mgmt_ip)
        self.gateway = QLineEdit(dev.gateway)
        self.gateway.setPlaceholderText("напр. 10.0.0.1")
        f_mgmt.addRow("Шлюз по умолчанию:", self.gateway)
        lay_ip.addLayout(f_mgmt)

        lay_ip.addWidget(QLabel("Статические маршруты («подсеть via next-hop»):"))
        self.routes = QPlainTextEdit("\n".join(f"{r['net']} via {r['via']}" for r in dev.routes))
        self.routes.setMaximumHeight(80)
        lay_ip.addWidget(self.routes)
        tabs.addTab(p_ip, "IP и сеть")

        # --- Вкладка 3: SNMP протокол ---
        p_snmp = QWidget()
        lay_snmp = QVBoxLayout(p_snmp)
        snmp_cfg = dev.snmp_params() if hasattr(dev, "snmp_params") else {}
        self.snmp_enabled = QCheckBox("Включить SNMP-агент на устройстве")
        self.snmp_enabled.setChecked(bool(snmp_cfg.get("enabled", True)))
        lay_snmp.addWidget(self.snmp_enabled)

        f_snmp = QFormLayout()
        self.snmp_comm = QLineEdit(str(snmp_cfg.get("community", "public")))
        f_snmp.addRow("Community (пароль опроса):", self.snmp_comm)
        self.snmp_port = QSpinBox()
        self.snmp_port.setRange(1, 65535)
        self.snmp_port.setValue(int(snmp_cfg.get("port", 161)))
        f_snmp.addRow("SNMP UDP-порт:", self.snmp_port)
        self.snmp_ver = QComboBox()
        self.snmp_ver.addItems(["v2c", "v1", "v3"])
        self.snmp_ver.setCurrentText(str(snmp_cfg.get("version", "v2c")))
        f_snmp.addRow("Версия SNMP:", self.snmp_ver)
        self.snmp_name = QLineEdit(str(snmp_cfg.get("sys_name", dev.name)))
        f_snmp.addRow("sysName (имя узла):", self.snmp_name)
        self.snmp_loc = QLineEdit(str(snmp_cfg.get("sys_location", f"{dev.lat:.6f}, {dev.lon:.6f}")))
        f_snmp.addRow("sysLocation (место):", self.snmp_loc)
        self.snmp_contact = QLineEdit(str(snmp_cfg.get("sys_contact", "noc@zonem.local")))
        f_snmp.addRow("sysContact (контакт):", self.snmp_contact)
        self.snmp_descr = QLineEdit(str(snmp_cfg.get("sys_descr", dev.spec["name"])))
        f_snmp.addRow("sysDescr (описание):", self.snmp_descr)
        lay_snmp.addLayout(f_snmp)
        lay_snmp.addStretch(1)
        tabs.addTab(p_snmp, "SNMP протокол")

        lay.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _parse(self):
        ips = {}
        for r, port in enumerate(self.ip_ports):
            text = (self.ip_table.item(r, 1).text() or "").strip()
            if text:
                ipaddress.ip_interface(text)
                ips[port] = text
        mgmt = self.mgmt_ip.text().strip()
        if mgmt:
            ipaddress.ip_interface(mgmt)
            ips["mgmt"] = mgmt
        gw = self.gateway.text().strip()
        if gw:
            ipaddress.ip_address(gw)
        routes = []
        for line in self.routes.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.replace(" via ", " ").split()
            if len(parts) != 2:
                raise ValueError(f"Маршрут «{line}»: ожидается «подсеть via next-hop»")
            ipaddress.ip_network(parts[0], strict=False)
            ipaddress.ip_address(parts[1])
            routes.append({"net": parts[0], "via": parts[1]})
        snmp_dict = {
            "enabled": self.snmp_enabled.isChecked(),
            "community": self.snmp_comm.text().strip() or "public",
            "port": self.snmp_port.value(),
            "version": self.snmp_ver.currentText(),
            "sys_name": self.snmp_name.text().strip(),
            "sys_location": self.snmp_loc.text().strip(),
            "sys_contact": self.snmp_contact.text().strip(),
            "sys_descr": self.snmp_descr.text().strip(),
        }
        return ips, gw, routes, snmp_dict

    def accept(self):
        try:
            self._result = self._parse()
        except ValueError as exc:
            QMessageBox.warning(self, "Ошибка в настройках", str(exc))
            return
        super().accept()

    def apply(self):
        """Записывает настройки в устройство."""
        ips, gw, routes, snmp_dict = self._result
        d = self.dev
        if d.tower_id is None:
            d.name = self.name.text().strip() or d.name
        d.power_mode = "poe" if self.power.currentIndex() == 1 else "local"
        if d.role == "radio":
            d.mount_h = self.mount.value()
        if self.radio_box is not None:
            d.radio = {"tech": self.tech.currentText(), "bw_mhz": self.bw.value(), "layers": self.layers.value(),
                       "max_users": self.max_users.value()}
        d.ips, d.gateway, d.routes = ips, gw, routes
        d.snmp = snmp_dict


class PortChooserDialog(QDialog):
    """Выбор свободных портов для кабеля с прогнозом результата."""

    def __init__(self, net, a_id, b_id, cable_id, building_data, parent=None):
        super().__init__(parent)
        self.net, self.a_id, self.b_id, self.cable_id, self.bd = net, a_id, b_id, cable_id, building_data
        cable = NC.CABLES[cable_id]
        self.setWindowTitle(f"Порты для кабеля «{cable['name']}»")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.cmb = []
        for did, label in ((a_id, "A"), (b_id, "B")):
            dev = net.devices[did]
            c = QComboBox()
            for p in net.free_ports(did):
                if net.port_accepts_cable(dev, p, cable):
                    c.addItem(f"{p.name} · {p.kind} {fmt_speed(p.mbps)}" + (" · PoE" if p.pse else ""), p.name)
            c.currentIndexChanged.connect(self._update)
            form.addRow(f"{dev.name} ({label}):", c)
            self.cmb.append(c)
        lay.addLayout(form)
        self.forecast = QLabel()
        self.forecast.setWordWrap(True)
        self.forecast.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.forecast)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update()

    def ports(self):
        return self.cmb[0].currentData(), self.cmb[1].currentData()

    def _update(self, *_):
        pa, pb = self.ports()
        if pa is None or pb is None:
            self.forecast.setText("Нет подходящих свободных портов.")
            return
        st = self.net._eval_link(Link("tmp", self.a_id, pa, self.b_id, pb, self.cable_id), self.bd)
        text, color = STATE_TEXT[st.state]
        speed = f"{fmt_speed(st.speed_mbps)}, " if st.speed_mbps else ""
        self.forecast.setText(f"Прогноз: <b style='color:{color}'>{text}</b> — {speed}длина {st.length_m:.0f} м. {st.reason}")


class SnmpDialog(QDialog):
    """Окно SNMP-опроса: заголовок соединения и таблицы MIB (как в браузере MIB/системе мониторинга)."""

    IF_HEAD = ("ifIndex", "ifDescr", "Тип", "Скорость", "MAC", "Admin", "Oper", "In (HC)", "Out (HC)",
               "Ошибки In", "ifAlias")

    def __init__(self, poll_fn, theme, dev=None, net=None, parent=None):
        super().__init__(parent)
        self.poll_fn, self.theme, self.dev, self.net = poll_fn, theme, dev, net
        self.setMinimumSize(940, 620)
        lay = QVBoxLayout(self)

        cfg = dev.snmp_params() if dev and hasattr(dev, "snmp_params") else {}
        bar = QHBoxLayout()
        bar.addWidget(QLabel("IP:"))
        init_ip = snmp._first_ip(dev) if dev else "127.0.0.1"
        self.ed_ip = QLineEdit(init_ip or "127.0.0.1")
        self.ed_ip.setMaximumWidth(130)
        bar.addWidget(self.ed_ip)
        bar.addWidget(QLabel("Порт:"))
        self.ed_port = QSpinBox()
        self.ed_port.setRange(1, 65535)
        self.ed_port.setValue(int(cfg.get("port", 161)))
        bar.addWidget(self.ed_port)
        bar.addWidget(QLabel("Community:"))
        self.ed_comm = QLineEdit(str(cfg.get("community", "public")))
        self.ed_comm.setMaximumWidth(100)
        bar.addWidget(self.ed_comm)
        bar.addWidget(QLabel("Версия:"))
        self.ed_ver = QComboBox()
        self.ed_ver.addItems(["v2c", "v1", "v3"])
        self.ed_ver.setCurrentText(str(cfg.get("version", "v2c")))
        bar.addWidget(self.ed_ver)
        b_poll = QPushButton("Опросить")
        b_poll.clicked.connect(self.refresh)
        bar.addWidget(b_poll)
        if dev and net:
            b_cfg = QPushButton("Настройки SNMP…")
            b_cfg.clicked.connect(self._open_config)
            bar.addWidget(b_cfg)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.header)
        self.tabs = QTabWidget()
        self.t_sys = self._table(("OID", "Объект", "Значение"))
        self.t_if = self._table(self.IF_HEAD)
        self.t_ip = self._table(("Адрес / сеть", "Маска / next-hop", "ifIndex / порт", "Тип"))
        self.t_lldp = self._table(("Локальный порт", "lldpRemSysName", "lldpRemPortId", "lldpRemSysDesc",
                                   "Адрес управления", "Линия"))
        self.t_extra = self._table(("OID", "Объект", "Значение"))
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        for w, title in ((self.t_sys, "Система"), (self.t_if, "Интерфейсы (ifTable)"), (self.t_ip, "IP и маршруты"),
                         (self.t_lldp, "Соседи (LLDP)"), (self.t_extra, "Доп."), (self.raw, "snmpwalk")):
            self.tabs.addTab(w, title)
        lay.addWidget(self.tabs, stretch=1)
        row = QHBoxLayout()
        b_refresh = QPushButton("Обновить (snmpwalk)")
        b_refresh.clicked.connect(self.refresh)
        b_copy = QPushButton("Копировать вывод")
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.result.walk_text()))
        b_close = QPushButton("Закрыть")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_refresh)
        row.addWidget(b_copy)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)
        self.result = None
        self.refresh()

    def _open_config(self):
        if not self.dev or not self.net:
            return
        dlg = DeviceDialog(self.net, self.dev, self)
        if dlg.exec():
            dlg.apply()
            cfg = self.dev.snmp_params()
            self.ed_comm.setText(str(cfg.get("community", "public")))
            self.ed_port.setValue(int(cfg.get("port", 161)))
            self.ed_ver.setCurrentText(str(cfg.get("version", "v2c")))
            ip = snmp._first_ip(self.dev)
            if ip:
                self.ed_ip.setText(ip)
            self.refresh()

    @staticmethod
    def _table(head):
        t = QTableWidget(0, len(head))
        t.setHorizontalHeaderLabels(list(head))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        return t

    @staticmethod
    def _fill(table, rows, colors=None):
        table.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if colors and colors[r]:
                    item.setForeground(QColor(colors[r]))
                table.setItem(r, c, item)

    def refresh(self):
        target_ip, port, comm, ver = None, None, None, None
        if hasattr(self, "ed_ip"):
            t = self.ed_ip.text()
            if isinstance(t, str) and t.strip():
                target_ip = t.strip()
        if hasattr(self, "ed_port"):
            p = self.ed_port.value()
            if isinstance(p, int):
                port = p
        if hasattr(self, "ed_comm"):
            c = self.ed_comm.text()
            if isinstance(c, str) and c.strip():
                comm = c.strip()
        if hasattr(self, "ed_ver"):
            v = self.ed_ver.currentText()
            if isinstance(v, str) and v.strip():
                ver = v.strip()
        try:
            res = self.result = self.poll_fn(target_ip=target_ip, port=port, community=comm, version=ver)
        except TypeError:
            res = self.result = self.poll_fn()
        self.setWindowTitle(f"SNMP: {res.name}")
        ok_color = "#2ecc71" if res.reachable and res.transport == "network" else (
            "#f5a524" if res.reachable else "#e5484d")
        path = " → ".join(res.path) if res.path else "—"
        comm_name = comm or (self.dev.snmp_params()["community"] if self.dev and hasattr(self.dev, "snmp_params") else snmp.COMMUNITY)
        self.header.setText(
            f"<b style='font-size:14px'>{res.name}</b> &nbsp; <code>{res.command}</code><br>"
            f"<span style='color:{ok_color}'><b>{res.status}</b></span><br>"
            f"Менеджер: {res.manager} · SNMP {ver or 'v2c'}, community «{comm_name}» · путь запроса: {path}"
            + (f"<br>sysUpTime: {snmp.fmt_ticks(res.uptime_s).split(') ')[-1]}" if res.reachable else ""))
        self._fill(self.t_sys, [(o, n.split("::")[-1], v) for o, n, v in res.system])
        colors = ["#e5484d" if not r["ifOperStatus"].startswith("up") and r["ifAlias"] else
                  ("#8a93a0" if not r["ifAlias"] else None) for r in res.interfaces]
        self._fill(self.t_if, [(r["ifIndex"], r["ifDescr"], r["ifType"], r["speed_text"], r["ifPhysAddress"],
                                r["ifAdminStatus"], r["ifOperStatus"], snmp.fmt_octets(r["ifHCInOctets"]),
                                snmp.fmt_octets(r["ifHCOutOctets"]), r["ifInErrors"], r["ifAlias"])
                               for r in res.interfaces], colors)
        ip_rows = [(f"{a['addr']}/{a['prefix']}", a["mask"], f"{a['ifIndex']} ({a['port']})", "ipAdEntAddr")
                   for a in res.ip_addrs]
        ip_rows += [(r["dest"], r["next_hop"], r["port"] or "—", f"маршрут {r['proto']}") for r in res.routes]
        self._fill(self.t_ip, ip_rows)
        self._fill(self.t_lldp, [(n["local_port"], n["sys_name"], n["port_id"], n["sys_desc"], n["man_addr"] or "—",
                                  n["cable"]) for n in res.neighbors])
        self._fill(self.t_extra, [(o, n.split("::")[-1], v) for o, n, v in res.extra])
        self.tabs.setTabText(4, res.extra_title or "Доп.")
        self.tabs.setTabEnabled(4, bool(res.extra))
        self.raw.setPlainText(res.walk_text())


# --------------------------------------------------------------------------- #
#  График нагрузки
# --------------------------------------------------------------------------- #
class LoadChart(QWidget):
    stepSelected = pyqtSignal(int)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.offered, self.served, self.sel = [], [], 0
        self.setMinimumHeight(150)

    def set_data(self, offered, served, sel=0):
        self.offered, self.served, self.sel = list(offered), list(served), sel
        self.update()

    def _geom(self):
        return QRectF(52, 10, max(self.width() - 64, 10), max(self.height() - 34, 10))

    def mousePressEvent(self, ev):
        n = len(self.offered)
        if n:
            area = self._geom()
            k = (ev.position().x() - area.left()) / max(area.width(), 1)
            self.stepSelected.emit(int(min(max(round(k * (n - 1)), 0), n - 1)))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        p.fillRect(self.rect(), QColor(t["bg1"]))
        area = self._geom()
        n = len(self.offered)
        top = max(max(self.offered, default=1.0), 1.0) * 1.08
        p.setPen(QPen(QColor(t["border"]), 1))
        font = p.font()
        font.setPointSizeF(8.5)
        p.setFont(font)
        for i in range(5):
            y = area.bottom() - area.height() * i / 4
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            p.setPen(QColor(t["dim"]))
            p.drawText(QRectF(0, y - 8, 48, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{top * i / 4:.0f}")
            p.setPen(QPen(QColor(t["border"]), 1))
        if n < 2:
            p.end()
            return

        def pts(vals):
            return QPolygonF([QPointF(area.left() + area.width() * i / (n - 1),
                                      area.bottom() - area.height() * v / top) for i, v in enumerate(vals)])
        p.setPen(QPen(QColor("#8a93a0"), 2, Qt.PenStyle.DashLine))
        p.drawPolyline(pts(self.offered))
        p.setPen(QPen(QColor("#2ecc71"), 2.5))
        p.drawPolyline(pts(self.served))
        x = area.left() + area.width() * self.sel / (n - 1)
        p.setPen(QPen(QColor(t["accent"]), 1.5))
        p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        p.setPen(QColor(t["dim"]))
        p.drawText(QRectF(area.left(), area.bottom() + 4, area.width(), 16), Qt.AlignmentFlag.AlignLeft,
                   "Мбит/с по шагам; пунктир — запрошено, зелёная — доставлено")
        p.end()


# --------------------------------------------------------------------------- #
#  Поток теста нагрузки
# --------------------------------------------------------------------------- #
class LoadWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, net, towers, bd, cfg):
        super().__init__()
        self.net, self.towers, self.bd, self.cfg = net, towers, bd, cfg
        self.cancelled = False

    def run(self):
        def cb(i, n):
            if self.cancelled:
                raise InterruptedError()
            self.progress.emit(i, n)
        try:
            self.done.emit(loadtest.run_load_test(self.net, self.towers, self.bd, self.cfg, cb))
        except InterruptedError:
            self.failed.emit("")
        except Exception as exc:                      # ошибка модели не должна ронять приложение
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
#  Примесь для главного окна
# --------------------------------------------------------------------------- #
class NetworkMixin:
    # ------------------------------ состояние ------------------------------ #
    def _net_init(self):
        self.net = Network()
        self.net_dev_model = "sw_l2_24poe"
        self.net_cable = "auto"
        self._net_sel = None                # ("dev", id) | ("link", id)
        self._ping_log = []
        self._load_result = None
        self._load_worker = None

    def net_persist(self):
        self._persist_due = False
        self.db.set_meta("network", self.net.to_dict())

    def _net_persist_later(self):
        """Перетаскивание шлёт события подряд — сохраняем в БД один раз, через 0.4 с после последнего."""
        self._persist_due = True
        if getattr(self, "_persist_timer", None) is None:
            self._persist_timer = QTimer(self)
            self._persist_timer.setSingleShot(True)
            self._persist_timer.timeout.connect(self.net_flush_persist)
        self._persist_timer.start(400)

    def net_flush_persist(self):
        if getattr(self, "_persist_due", False):
            self.net_persist()

    def net_load_from_db(self):
        data = self.db.get_meta("network")
        if data:
            try:
                self.net = Network.from_dict(data)
            except (TypeError, KeyError, ValueError):
                self.net = Network()

    def net_after_change(self, persist=True):
        self.net.evaluate(self.building_data)
        if persist:
            self.net_persist()
        else:
            self._net_persist_later()
        self._push_net()
        self._render_net_panels()
        self.lbl_net.setText(self._net_summary_text())

    def net_after_towers_changed(self):
        # вышки, убранные «Очистить карту», остаются в БД — их БС и кабели не удаляем
        self.net.sync_towers(self.towers, keep_ids=self.db.tower_ids())
        self.net.evaluate(self.building_data)
        self.net_persist()
        self._push_net()
        self._render_net_panels()
        self.lbl_net.setText(self._net_summary_text())

    def _net_summary_text(self):
        links = self.net.status.values()
        down = sum(1 for s in links if s.state == "down")
        devs = sum(1 for d in self.net.devices.values() if d.tower_id is None)
        return f"Сеть: устройств {devs}, линков {len(self.net.links)}" + (f", ✕ {down}" if down else "")

    def _push_net(self):
        visible = {t.db_id for t in self.towers}
        self._js(f"api.setNetwork({json.dumps(self.net.to_map(visible_towers=visible))});")

    # ------------------------------ палитры ------------------------------ #
    def build_network_palette(self):
        page = QWidget()
        page.setObjectName("panel")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(6)
        lay.addWidget(self._section("Устройства"))
        self.dev_tree = QTreeWidget()
        self.dev_tree.setHeaderHidden(True)
        for role, title in NC.ROLE_NAMES.items():
            if role == "tower":
                continue                                            # БС ставятся вкладкой «Радио»
            top = QTreeWidgetItem([title])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for mid, m in NC.MODELS.items():
                if m["role"] == role:
                    it = QTreeWidgetItem([m["name"]])
                    it.setData(0, Qt.ItemDataRole.UserRole, mid)
                    top.addChild(it)
            self.dev_tree.addTopLevelItem(top)
        self.dev_tree.expandAll()
        self.dev_tree.itemClicked.connect(self._on_dev_palette)
        lay.addWidget(self.dev_tree, stretch=3)
        self.dev_detail = QLabel()
        self.dev_detail.setWordWrap(True)
        self.dev_detail.setProperty("role", "dim")
        lay.addWidget(self.dev_detail)

        lay.addWidget(self._section("Кабели и линии связи"))
        self.cable_tree = QTreeWidget()
        self.cable_tree.setHeaderHidden(True)
        auto = QTreeWidgetItem(["★ Авто — подобрать лучший кабель"])
        auto.setData(0, Qt.ItemDataRole.UserRole, "auto")
        self.cable_tree.addTopLevelItem(auto)
        for grp, title in NC.GROUP_NAMES.items():
            top = QTreeWidgetItem([title])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for cid, c in NC.CABLES.items():
                if c["group"] == grp:
                    it = QTreeWidgetItem([c["name"]])
                    it.setData(0, Qt.ItemDataRole.UserRole, cid)
                    top.addChild(it)
            self.cable_tree.addTopLevelItem(top)
        self.cable_tree.expandItem(self.cable_tree.topLevelItem(0))
        self.cable_tree.setCurrentItem(auto)
        self.cable_tree.itemClicked.connect(self._on_cable_palette)
        lay.addWidget(self.cable_tree, stretch=4)
        self.cable_detail = QLabel("Авто: программа сама выберет порты и кабель — максимальная скорость, затем дешевле.")
        self.cable_detail.setWordWrap(True)
        self.cable_detail.setProperty("role", "dim")
        lay.addWidget(self.cable_detail)
        self.chk_manual_ports = QCheckBox("Выбирать порты вручную")
        lay.addWidget(self.chk_manual_ports)
        row = QHBoxLayout()
        b1 = QPushButton("Авто-IP и маршруты")
        b1.setToolTip("Раздать IP на подключённых портах и построить маршруты (как OSPF)")
        b1.clicked.connect(self.net_autoconfigure)
        b2 = QPushButton("Спецификация…")
        b2.clicked.connect(self.net_export_bom)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        default = next((it for i in range(self.dev_tree.topLevelItemCount())
                        for it in (self.dev_tree.topLevelItem(i).child(k) for k in range(self.dev_tree.topLevelItem(i).childCount()))
                        if it.data(0, Qt.ItemDataRole.UserRole) == self.net_dev_model), None)
        if default is not None:
            self.dev_tree.setCurrentItem(default)
            self._on_dev_palette(default)
        return page

    def _on_dev_palette(self, item, *_):
        mid = item.data(0, Qt.ItemDataRole.UserRole)
        if mid is None:
            return
        self.net_set_model(mid, activate=False)

    def net_set_model(self, mid, activate=True):
        self.net_dev_model = mid
        m = NC.MODELS[mid]
        groups = ", ".join(f"{g['n']}×{g['kind']} {fmt_speed(g['mbps'])}" + (f" PoE {g['pse']}" if g["pse"] else "")
                           for g in m["ports"])
        poe = f"; PoE-бюджет {m['poe_budget_w']:g} Вт" if m["poe_budget_w"] else ""
        self.dev_detail.setText(f"{m['name']}\nПорты: {groups}{poe}.\nПотребляет ~{m['power_w']:g} Вт, ≈ ${m['cost']:,}.\n{m['example']}")
        if activate:
            self._mode_actions["device"].setChecked(True)

    def _on_cable_palette(self, item, *_):
        cid = item.data(0, Qt.ItemDataRole.UserRole)
        if cid is not None:
            self.net_set_cable(cid, activate=False)

    def net_set_cable(self, cid, activate=True):
        self.net_cable = cid
        if cid == "auto":
            self.cable_detail.setText("Авто: программа сама выберет порты и кабель — максимальная скорость, затем дешевле.")
        else:
            c = NC.CABLES[cid]
            if c["group"] == "copper":
                spec = "; ".join(f"{fmt_speed(s)} до {m} м" for s, m in c["tiers"][:4])
            elif c["group"] == "radio":
                b = NC.RADIO_BANDS[c["band"]]
                spec = f"{b['freq_ghz']:g} ГГц, до {b['max_km']:g} км, до {b['ladder'][-1][1] / 1000:g} Гбит/с"
            else:
                spec = f"{fmt_speed(c['mbps'])}, до {c['max_m']:g} м ({c['form']})"
            self.cable_detail.setText(f"{c['name']}\n{spec}. {c['note']}.")
        if activate:
            self._mode_actions["cable"].setChecked(True)

    def build_device_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for role, title in NC.ROLE_NAMES.items():
            if role == "tower":
                continue
            sub = menu.addMenu(title)
            for mid, m in NC.MODELS.items():
                if m["role"] == role:
                    act = sub.addAction(m["name"])
                    act.triggered.connect(lambda checked=False, mid=mid: self.net_set_model(mid))
        return menu

    def build_cable_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act = menu.addAction("★ Авто — подобрать лучший")
        act.triggered.connect(lambda checked=False: self.net_set_cable("auto"))
        for grp, title in NC.GROUP_NAMES.items():
            sub = menu.addMenu(title)
            for cid, c in NC.CABLES.items():
                if c["group"] == grp:
                    act = sub.addAction(c["name"])
                    act.triggered.connect(lambda checked=False, cid=cid: self.net_set_cable(cid))
        return menu

    # ------------------------------ вкладки справа ------------------------------ #
    def build_network_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.net_info = QTextBrowser()
        self.net_info.setPlaceholderText("Выберите устройство или кабель на карте (режим «Навигация»)")
        tl.addWidget(self.net_info)
        row = QHBoxLayout()
        self.btn_net_cfg = QPushButton("Настроить…")
        self.btn_net_cfg.clicked.connect(self.net_configure_selected)
        self.btn_net_del = QPushButton("Удалить")
        self.btn_net_del.setProperty("danger", True)
        self.btn_net_del.clicked.connect(self.net_delete_selected)
        self.btn_net_snmp = QPushButton("SNMP…")
        self.btn_net_snmp.setToolTip("Опросить выбранное устройство по SNMP")
        self.btn_net_snmp.clicked.connect(self.snmp_selected)
        row.addWidget(self.btn_net_cfg)
        row.addWidget(self.btn_net_snmp)
        row.addWidget(self.btn_net_del)
        tl.addLayout(row)
        split.addWidget(top)
        self.link_table = QTableWidget(0, 4)
        self.link_table.setHorizontalHeaderLabels(["Кабель", "Соединение", "Длина", "Скорость"])
        self.link_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.link_table.verticalHeader().setVisible(False)
        self.link_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.link_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.link_table.itemSelectionChanged.connect(self._on_link_table)
        split.addWidget(self.link_table)
        self.ping_log = QTextBrowser()
        self.ping_log.setPlaceholderText("Журнал PDU: режим «PDU» — кликните источник и получателя")
        split.addWidget(self.ping_log)
        split.setSizes([260, 130, 120])
        lay.addWidget(split)
        return page

    def build_load_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 4)
        lay.setSpacing(6)
        form = QFormLayout()
        self.ld_users = QSpinBox()
        self.ld_users.setRange(20, 5000)
        self.ld_users.setValue(600)
        self.ld_active = QSpinBox()
        self.ld_active.setRange(1, 100)
        self.ld_active.setValue(30)
        self.ld_active.setSuffix(" %")
        self.ld_steps = QSpinBox()
        self.ld_steps.setRange(3, 60)
        self.ld_steps.setValue(20)
        self.ld_scn = QComboBox()
        for key, (name, _mix) in loadtest.SCENARIOS.items():
            self.ld_scn.addItem(name, key)
        self.ld_indoor = QSpinBox()
        self.ld_indoor.setRange(0, 100)
        self.ld_indoor.setValue(70)
        self.ld_indoor.setSuffix(" %")
        form.addRow("Абонентов:", self.ld_users)
        form.addRow("Активных одновременно:", self.ld_active)
        form.addRow("Шагов (по ~1 мин):", self.ld_steps)
        form.addRow("Сценарий:", self.ld_scn)
        form.addRow("Абонентов в зданиях:", self.ld_indoor)
        lay.addLayout(form)
        self.ld_hot = QCheckBox("Горячие точки (40% абонентов скучены)")
        self.ld_hot.setChecked(True)
        self.ld_ip = QCheckBox("Учитывать IP-маршрутизацию (после «Авто-IP»)")
        lay.addWidget(self.ld_hot)
        lay.addWidget(self.ld_ip)
        row = QHBoxLayout()
        self.btn_ld_start = QPushButton("▶  Запустить тест нагрузки")
        self.btn_ld_start.setProperty("primary", True)
        self.btn_ld_start.clicked.connect(self.run_load_test)
        self.btn_ld_clear = QPushButton("Сбросить")
        self.btn_ld_clear.clicked.connect(self.clear_load_results)
        row.addWidget(self.btn_ld_start, stretch=2)
        row.addWidget(self.btn_ld_clear)
        lay.addLayout(row)
        self.ld_progress = QProgressBar()
        self.ld_progress.setRange(0, 100)
        self.ld_progress.setTextVisible(False)
        self.ld_progress.hide()
        lay.addWidget(self.ld_progress)
        self.ld_chart = LoadChart(self.th)
        self.ld_chart.stepSelected.connect(self._ld_select_step)
        lay.addWidget(self.ld_chart)
        srow = QHBoxLayout()
        self.ld_slider = QSlider(Qt.Orientation.Horizontal)
        self.ld_slider.setRange(0, 0)
        self.ld_slider.valueChanged.connect(self._ld_select_step)
        self.ld_step_lbl = QLabel("шаг —")
        self.ld_step_lbl.setProperty("role", "dim")
        srow.addWidget(self.ld_slider, stretch=1)
        srow.addWidget(self.ld_step_lbl)
        lay.addLayout(srow)
        self.ld_summary = QTextBrowser()
        self.ld_summary.setMinimumHeight(260)
        self.ld_summary.setPlaceholderText(
            "Тест раскладывает абонентов по участку, подключает их к вышкам, пускает трафик по кабелям "
            "и показывает загрузку, потери и задержки. Результат рисуется на карте.")
        lay.addWidget(self.ld_summary, stretch=1)
        self.btn_ld_export = QPushButton("Сохранить отчёт (CSV)…")
        self.btn_ld_export.clicked.connect(self.export_load_report)
        lay.addWidget(self.btn_ld_export)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    # ------------------------------ операции с сетью ------------------------------ #
    def _next_dev_name(self, model_id):
        role = NC.MODELS[model_id]["role"]
        pref = ROLE_PREFIX.get(role, "DEV")
        used = {d.name for d in self.net.devices.values()}
        n = 1
        while f"{pref}-{n}" in used:
            n += 1
        return f"{pref}-{n}"

    def net_place_device(self, lat, lon):
        if self._mode != "device":
            return
        mid = self.net_dev_model
        dev = self.net.add_device(mid, self._next_dev_name(mid), lat, lon)
        self.net_after_change()
        self.net_select_device(dev.id, switch_tab=False)
        self.statusBar().showMessage(f"Добавлено: {dev.name} ({NC.MODELS[mid]['name']})")

    def net_move_device(self, did, lat, lon):
        dev = self.net.devices.get(did)
        if dev and dev.tower_id is None:
            dev.lat, dev.lon = lat, lon
            self.net_after_change(persist=False)

    def net_select_device(self, did, switch_tab=True):
        self._net_sel = ("dev", did) if did in self.net.devices else None
        self._js(f"api.selectDevice({json.dumps(did)});")
        self._update_net_selection_ui()
        if switch_tab and self._net_sel:
            self.tabs.setCurrentWidget(self.net_page)

    def net_select_link(self, lid, from_table=False):
        self._net_sel = ("link", lid) if lid in self.net.links else None
        self._js(f"api.selectLink({json.dumps(lid)});")
        self._update_net_selection_ui(sync_table=not from_table)
        if self._net_sel:
            self.tabs.setCurrentWidget(self.net_page)

    def net_on_tower_selected(self, tid):
        did = f"T{tid}" if tid is not None else None
        self._net_sel = ("dev", did) if did in self.net.devices else None
        self._update_net_selection_ui()

    def net_connect(self, a, b):
        net = self.net
        if a not in net.devices or b not in net.devices or a == b:
            return
        da, db = net.devices[a], net.devices[b]
        if self.net_cable == "auto":
            best = net.suggest_cable(a, b, self.building_data)
            if not best:
                QMessageBox.warning(self, "Кабель", f"Между «{da.name}» и «{db.name}» нет рабочего кабеля: не хватает "
                                    "свободных совместимых портов или расстояние больше дальности любого кабеля.")
                return
            cid, pa, pb = best["cable"], best["a_port"], best["b_port"]
        else:
            cid = self.net_cable
            cable = NC.CABLES[cid]
            fa = [p for p in net.free_ports(a) if net.port_accepts_cable(da, p, cable)]
            fb = [p for p in net.free_ports(b) if net.port_accepts_cable(db, p, cable)]
            if not fa or not fb:
                who = da.name if not fa else db.name
                QMessageBox.warning(self, "Кабель", f"У «{who}» нет свободного порта, подходящего для «{cable['name']}».")
                return
            if self.chk_manual_ports.isChecked():
                dlg = PortChooserDialog(net, a, b, cid, self.building_data, self)
                if not dlg.exec():
                    return
                pa, pb = dlg.ports()
            else:
                # раньше брались первые подходящие порты — например, 1G-порт вместо свободного 10G
                # или порт, на котором линк «не поднимается»; теперь — лучшая пара для этого кабеля
                best = net.suggest_cable(a, b, self.building_data, cable_id=cid)
                pa, pb = (best["a_port"], best["b_port"]) if best else (fa[0].name, fb[0].name)
        link, err = net.connect(a, pa, b, pb, cid)
        if link is None:
            QMessageBox.warning(self, "Кабель", err)
            return
        self._net_sel = ("link", link.id)
        self.net_after_change()
        self._js(f"api.selectLink({json.dumps(link.id)});")
        if hasattr(self, "_mode_actions") and "navigate" in self._mode_actions:
            self._mode_actions["navigate"].setChecked(True)
        self.tabs.setCurrentWidget(self.net_page)
        st = net.status[link.id]
        text, _ = STATE_TEXT[st.state]
        self.statusBar().showMessage(f"{NC.CABLES[cid]['name']}: {da.name}:{pa} ↔ {db.name}:{pb} — {text}"
                                     + (f", {fmt_speed(st.speed_mbps)}" if st.speed_mbps else "")
                                     + (f". {st.reason}" if st.reason else ""))

    def net_delete_selected(self):
        if not self._net_sel:
            return
        kind, key = self._net_sel
        if kind == "link":
            self.net.remove_link(key)
        else:
            dev = self.net.devices.get(key)
            if dev is None:
                return
            if dev.tower_id is not None:
                QMessageBox.information(self, "Удаление", "Базовая станция удаляется на вкладке «Вышка» (кнопка «Удалить»). "
                                        "Здесь можно удалить её кабели — выберите кабель.")
                return
            if QMessageBox.question(self, "Удаление", f"Удалить «{dev.name}» вместе с кабелями?") != QMessageBox.StandardButton.Yes:
                return
            self.net.remove_device(key)
        self._net_sel = None
        self._js("api.selectDevice(null); api.selectLink(null);")
        self.net_after_change()

    def net_configure_selected(self):
        if not self._net_sel or self._net_sel[0] != "dev":
            QMessageBox.information(self, "Настройка", "Выберите устройство на карте.")
            return
        dev = self.net.devices.get(self._net_sel[1])
        if dev is None:
            return
        dlg = DeviceDialog(self.net, dev, self)
        if dlg.exec():
            dlg.apply()
            self.net_after_change()

    def net_autoconfigure(self):
        if not self.net.links:
            QMessageBox.information(self, "Авто-IP", "Сначала соедините устройства кабелями.")
            return
        self.net.evaluate(self.building_data)
        info = self.net.autoconfigure()
        self.net_after_change()
        self.statusBar().showMessage(f"Авто-IP: подсетей {info['segments']}, устройств с адресами {info['devices_with_ip']}. "
                                     "Маршруты построены. Проверьте связь режимом PDU.")

    def net_ping(self, a, b):
        net = self.net
        if a not in net.devices or b not in net.devices or a == b:
            return
        res = net.ping(a, b)
        names = lambda ids: " → ".join(net.devices[i].name for i in ids)
        da, db = net.devices[a].name, net.devices[b].name
        stamp = time.strftime("%H:%M:%S")
        if res.ok:
            entry = (f"<p><span style='color:#2ecc71'><b>✓ {stamp}</b></span> {da} → {db} ({res.dst_ip}): "
                     f"RTT {res.rtt_ms:.2f} мс<br><span style='color:{self.th['dim']}'>туда: {names(res.hops)}"
                     f"<br>обратно: {names(res.back_hops)}</span></p>")
            fwd, back = res.hops, res.back_hops
            path = fwd + back[1:]
            label = f"RTT {res.rtt_ms:.2f} мс"
        else:
            entry = (f"<p><span style='color:#e5484d'><b>✗ {stamp}</b></span> {da} → {db}: {res.reason}"
                     f"<br><span style='color:{self.th['dim']}'>дошёл до: {names(res.hops) if res.hops else '—'}</span></p>")
            path = list(res.hops) if res.hops else [a]
            if res.back_hops:                                        # запрос дошёл, ответ — нет
                path = res.hops + res.back_hops[1:]
            label = "нет связи"
        self._ping_log.insert(0, entry)
        self._ping_log = self._ping_log[:25]
        self.ping_log.setHtml("".join(self._ping_log))
        coords = [[net.devices[i].lat, net.devices[i].lon] for i in path]
        self._js(f"api.animate({json.dumps(coords)}, {str(res.ok).lower()}, {json.dumps(label)});")
        self.tabs.setCurrentWidget(self.net_page)
        self.statusBar().showMessage(("PDU доставлен" if res.ok else "PDU не доставлен") + f": {da} → {db}")

    def snmp_selected(self):
        """SNMP для выбранного сетевого устройства или вышки."""
        did = None
        if self._net_sel and self._net_sel[0] == "dev":
            did = self._net_sel[1]
        elif self.selected_id is not None:
            did = f"T{self.selected_id}"
        if did is None:
            QMessageBox.information(self, "SNMP", "Выберите вышку или устройство на карте (или дважды кликните по ней).")
            return
        self.snmp_open(did)

    def _current_link_util(self):
        res = self._load_result
        if res is None or not res.steps:
            return None
        step = res.steps[min(max(self.ld_slider.value(), 0), len(res.steps) - 1)]
        return {lid: float(step.link_util[e]) for e, lid in enumerate(res.link_ids)}

    def snmp_open(self, did):
        if did is None or did not in self.net.devices:
            return
        self.net.evaluate(self.building_data)
        dev = self.net.devices[did]
        tower = next((t for t in self.towers if t.db_id == dev.tower_id), None) if dev.tower_id is not None else None
        poll = lambda target_ip=None, port=None, community=None, version=None: snmp.poll(
            self.net, did, link_util=self._current_link_util(), tower=tower,
            target_ip=target_ip, port=port, community=community, version=version)
        dlg = SnmpDialog(poll, self.th, dev=dev, net=self.net, parent=self)
        self._last_snmp = dlg.result
        if dlg.result:
            self.statusBar().showMessage(f"SNMP {dev.name}: {dlg.result.status}")
        dlg.exec()

    def net_export_bom(self):
        rows, total = self.net.bom()
        if not rows:
            QMessageBox.information(self, "Спецификация", "В проекте нет устройств и кабелей.")
            return
        path = self._save_dialog("Спецификация оборудования", "bom.csv", "CSV (*.csv)")
        if not path:
            return
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n", delimiter=";")
        w.writerow(["Категория", "Наименование", "Кол-во", "Цена за ед., $", "Сумма, $"])
        for r in rows:
            w.writerow([r[0], r[1], r[2], f"{r[3]:.2f}", f"{r[4]:.2f}"])
        w.writerow(["", "ИТОГО (оценка)", "", "", f"{total:.2f}"])
        try:
            Path(path).write_text("\ufeff" + buf.getvalue(), encoding="utf-8")
            self.statusBar().showMessage(f"Спецификация сохранена: {path} (итого ≈ ${total:,.0f})")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка записи", str(exc))

    # ------------------------------ отрисовка панелей ------------------------------ #
    def _render_net_panels(self):
        net = self.net
        self._block_link_table = True
        self.link_table.blockSignals(True)
        try:
            self.link_table.setRowCount(len(net.links))
            for r, (lid, l) in enumerate(net.links.items()):
                st = net.status.get(lid)
                c = NC.CABLES[l.cable]
                cells = (c["name"], f"{net.devices[l.a_dev].name}:{l.a_port} ↔ {net.devices[l.b_dev].name}:{l.b_port}",
                         f"{st.length_m:.0f} м" if st else "—",
                         (fmt_speed(st.speed_mbps) if st and st.speed_mbps else "✕"))
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, lid)
                    if st and st.state == "down":
                        item.setForeground(QColor("#e5484d"))
                    elif st and st.state == "warn":
                        item.setForeground(QColor("#f5a524"))
                    self.link_table.setItem(r, col, item)
        finally:
            self.link_table.blockSignals(False)
            self._block_link_table = False
        self._update_net_selection_ui(sync_table=True)

    def _update_net_selection_ui(self, sync_table=True):
        net = self.net
        sel = self._net_sel
        if sync_table:
            self._block_link_table = True
            self.link_table.blockSignals(True)
            try:
                if sel and sel[0] == "link":
                    found = False
                    for r in range(self.link_table.rowCount()):
                        item = self.link_table.item(r, 0)
                        if item and item.data(Qt.ItemDataRole.UserRole) == sel[1]:
                            self.link_table.selectRow(r)
                            found = True
                            break
                    if not found:
                        self.link_table.clearSelection()
                else:
                    self.link_table.clearSelection()
            finally:
                self.link_table.blockSignals(False)
                self._block_link_table = False
        self.btn_net_cfg.setEnabled(bool(sel and sel[0] == "dev"))
        self.btn_net_snmp.setEnabled(bool(sel and sel[0] == "dev"))
        self.btn_net_del.setEnabled(bool(sel))
        if not sel:
            self.net_info.setHtml("")
        elif sel[0] == "dev" and sel[1] in net.devices:
            self.net_info.setHtml(self._dev_html(net.devices[sel[1]]))
        elif sel[0] == "link" and sel[1] in net.links:
            self.net_info.setHtml(self._link_html(net.links[sel[1]]))
        else:
            self.net_info.setHtml("")

    def _on_link_table(self):
        if getattr(self, "_block_link_table", False):
            return
        rows = self.link_table.selectionModel().selectedRows()
        if rows:
            item = self.link_table.item(rows[0].row(), 0)
            if item is not None:
                self.net_select_link(item.data(Qt.ItemDataRole.UserRole), from_table=True)

    def _kv(self, rows):
        dim = self.th["dim"]
        return "<table cellpadding='2'>" + "".join(
            f"<tr><td style='color:{dim}; padding-right:10px'>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"

    def _dev_html(self, dev):
        net, st = self.net, self.net.dev_state.get(dev.id, {})
        power = ("<span style='color:#2ecc71'>работает</span>" if st.get("powered", True)
                 else f"<span style='color:#e5484d'>нет питания</span>: {st.get('reason', '')}")
        rows = [("Модель", dev.spec["name"]), ("Назначение", NC.ROLE_NAMES[dev.role]),
                ("Координаты", f"{dev.lat:.6f}, {dev.lon:.6f}"),
                ("Питание", f"{power} ({'PoE' if dev.power_mode == 'poe' else 'местное'})")]
        if dev.spec["poe_budget_w"]:
            rows.append(("PoE-бюджет", f"{st.get('poe_used_w', 0):.0f} / {dev.spec['poe_budget_w']:g} Вт"))
        if dev.role == "radio":
            rows.append(("Высота антенны", f"{dev.mount_h:g} м"))
        if dev.role == "tower":
            r = dev.radio
            rows.append(("Радио", f"{r.get('tech')}, {r.get('bw_mhz'):g} МГц, MIMO {r.get('layers')}×, до {r.get('max_users')} абонентов"))
        if dev.gateway:
            rows.append(("Шлюз", dev.gateway))
        used, port_rows = {}, []
        for p in dev.ports():
            l = net.port_link(dev.id, p.name)
            if not l:
                used[(p.kind, p.mbps)] = used.get((p.kind, p.mbps), 0) + 1
                continue
            peer_id, peer_port = (l.b_dev, l.b_port) if l.a_dev == dev.id else (l.a_dev, l.a_port)
            s = net.status.get(l.id)
            text, color = STATE_TEXT[s.state] if s else ("—", "#888")
            port_rows.append(f"<tr><td>{p.name}</td><td>{p.kind}</td><td style='color:{color}'>{text}"
                             f"{' ' + fmt_speed(s.speed_mbps) if s and s.speed_mbps else ''}</td>"
                             f"<td>→ {net.devices[peer_id].name}:{peer_port}</td><td>{dev.ips.get(p.name, '')}</td></tr>")
        html = f"<h3 style='margin:0 0 4px 0'>{dev.name}</h3>" + self._kv(rows)
        if port_rows:
            html += ("<p style='margin:6px 0 2px 0'><b>Подключения</b></p><table cellpadding='2'>"
                     "<tr><th align='left'>Порт</th><th align='left'>Тип</th><th align='left'>Состояние</th>"
                     "<th align='left'>К</th><th align='left'>IP</th></tr>" + "".join(port_rows) + "</table>")
        if used:
            free = ", ".join(f"{n}× {k} {fmt_speed(m)}" for (k, m), n in used.items())
            html += f"<p style='color:{self.th['dim']}'>Свободно: {free}</p>"
        return html

    def _link_html(self, link):
        net = self.net
        st = net.status.get(link.id)
        c = NC.CABLES[link.cable]
        a, b = net.devices[link.a_dev], net.devices[link.b_dev]
        text, color = STATE_TEXT[st.state] if st else ("—", "#888")
        rows = [("Кабель", c["name"]), ("Соединение", f"{a.name}:{link.a_port} ↔ {b.name}:{link.b_port}"),
                ("Состояние", f"<b style='color:{color}'>{text}</b>" + (f", {fmt_speed(st.speed_mbps)}" if st and st.speed_mbps else "")),
                ("Длина", f"{st.length_m:.0f} м ({'задана' if link.length_m is not None else 'по карте'})" if st else "—")]
        if st and st.reason:
            rows.append(("Причина", st.reason))
        if st and st.extra:
            e = st.extra
            rows += [("Диапазон", e["band"]), ("Уровень приёма", f"{e['rsl_dbm']:.1f} дБм (FSPL {e['fspl_db']:.1f} дБ)"),
                     ("Запас на дождь", f"{e['rain_margin_db']:.1f} дБ"),
                     ("Зона Френеля", f"{max(e['fresnel'], 0) * 100:.0f}% свободно" if e['fresnel'] < 9 else "нет данных о зданиях")]
        if st:
            cost = c["fixed"] + c["per_m"] * st.length_m
            rows.append(("Стоимость", f"≈ ${cost:,.0f}"))
        return f"<h3 style='margin:0 0 4px 0'>Линк {link.id}</h3>" + self._kv(rows)

    # ------------------------------ тест нагрузки ------------------------------ #
    def run_load_test(self):
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.cancelled = True
            return
        if not self.towers:
            QMessageBox.information(self, "Тест нагрузки", "Сначала поставьте хотя бы одну вышку.")
            return
        self.net.evaluate(self.building_data)
        cfg = loadtest.LoadConfig(
            n_users=self.ld_users.value(), active_share=self.ld_active.value() / 100.0, steps=self.ld_steps.value(),
            indoor_share=self.ld_indoor.value() / 100.0, hotspots=self.ld_hot.isChecked(),
            scenario=self.ld_scn.currentData(), require_ip=self.ld_ip.isChecked(),
            area=[tuple(p) for p in self.zone_algo] if self.zone_algo else None)
        bd = self.building_data if self.chk_buildings.isChecked() else None
        self._load_worker = LoadWorker(self.net, list(self.towers), bd, cfg)
        self._load_worker.progress.connect(lambda i, n: self.ld_progress.setValue(int(100 * i / n)))
        self._load_worker.done.connect(self._on_load_done)
        self._load_worker.failed.connect(self._on_load_failed)
        self.btn_ld_start.setText("■  Остановить")
        self.ld_progress.setValue(0)
        self.ld_progress.show()
        self.tabs.setCurrentWidget(self.load_page)
        self.statusBar().showMessage("Тест нагрузки выполняется…")
        self._load_worker.start()

    def _load_finished_ui(self):
        self.btn_ld_start.setText("▶  Запустить тест нагрузки")
        self.ld_progress.hide()

    def _on_load_failed(self, msg):
        self._load_finished_ui()
        if msg:
            QMessageBox.warning(self, "Тест нагрузки", msg)
        self.statusBar().showMessage("Тест нагрузки остановлен" if not msg else "Тест нагрузки не выполнен")

    def _on_load_done(self, res):
        self._load_finished_ui()
        self._load_result = res
        steps = res.steps
        self.ld_slider.blockSignals(True)
        self.ld_slider.setRange(0, len(steps) - 1)
        self.ld_slider.setValue(res.summary["peak_step"])
        self.ld_slider.blockSignals(False)
        self._render_load_summary()
        self._ld_select_step(res.summary["peak_step"])
        s = res.summary
        self.statusBar().showMessage(f"Тест нагрузки: доставлено {s['served_mean']:.0f} из {s['offered_mean']:.0f} Мбит/с, "
                                     f"не доставлено {s['undelivered_pct']:.0f}%")

    def _ld_select_step(self, i):
        res = self._load_result
        if res is None or not (0 <= i < len(res.steps)):
            return
        self.ld_slider.blockSignals(True)
        self.ld_slider.setValue(i)
        self.ld_slider.blockSignals(False)
        st = res.steps[i]
        self.ld_chart.set_data([s.offered_dl for s in res.steps], [s.served_dl for s in res.steps], i)
        self.ld_step_lbl.setText(f"шаг {i + 1}/{len(res.steps)}: активных {st.active}, ок {st.ok}, "
                                 f"урезано {st.degraded}")
        self._push_traffic(i)

    def _push_traffic(self, i):
        res = self._load_result
        st = res.steps[i]
        users = [[round(float(la), 6), round(float(lo), 6), int(s)]
                 for la, lo, s in zip(res.lat, res.lon, st.state)]
        lines = []
        t_served = {}
        for u in range(len(res.lat)):
            s = int(st.serving[u])
            if s >= 0 and st.state[u] != loadtest.STATE_IDLE:
                t_served.setdefault(s, []).append(u)
        for s, u_list in t_served.items():
            if s < len(res.tower_pos):
                tlat, tlon = res.tower_pos[s]
                stride = max(1, len(u_list) // 25)
                for u in u_list[::stride]:
                    lines.append([round(float(res.lat[u]), 6), round(float(res.lon[u]), 6), tlat, tlon,
                                  2 if st.state[u] == loadtest.STATE_DEGRADED else 1])
        util = {lid: round(float(st.link_util[e]), 3) for e, lid in enumerate(res.link_ids)}
        self._js(f"api.setTraffic({json.dumps({'users': users, 'lines': lines, 'linkUtil': util})});")

    def clear_load_results(self):
        self._load_result = None
        self.ld_summary.setHtml("")
        self.ld_chart.set_data([], [], 0)
        self.ld_slider.setRange(0, 0)
        self.ld_step_lbl.setText("шаг —")
        self._js("api.clearTraffic();")

    def _render_load_summary(self):
        res, s = self._load_result, self._load_result.summary
        dim = self.th["dim"]
        ok = s["undelivered_pct"] < 5
        verdict = (f"<span style='color:{'#2ecc71' if ok else '#e5484d'}'><b>{'Сеть справляется' if ok else 'Сеть перегружена'}</b></span>"
                   f" — не доставлено {s['undelivered_pct']:.1f}% трафика")
        mode = ("транспорт учтён (до ядра по линкам)" if res.transport_mode == "network"
                else "только радио: ядро не добавлено, транспорт не ограничивает")
        kpi = [("Абонентов / активных", f"{s['users']} / {s['active_mean']:.0f} в среднем"),
               ("Запрошено ↓", f"{_fmt_mbps(s['offered_mean'])} (пик {_fmt_mbps(s['offered_peak'])})"),
               ("Доставлено ↓", f"{_fmt_mbps(s['served_mean'])} (пик {_fmt_mbps(s['served_peak'])})"),
               ("Вверх (UL)", f"{_fmt_mbps(s['served_ul_mean'])} из {_fmt_mbps(s['offered_ul_mean'])}"),
               ("Качество", f"{s['ok_pct']:.0f}% ок, {s['degraded_pct']:.0f}% урезано, {s['blocked_pct']:.0f}% без связи"),
               ("Нет связи", f"вне покрытия {s['blocked_cov']:.0f}, лимит соты {s['blocked_cap']:.0f}, "
                             f"нет транспорта {s['blocked_transport']:.0f} (в среднем за шаг)"),
               ("Задержка", f"{s['lat_mean']:.1f} мс средняя, {s['lat_p95']:.1f} мс (p95, макс. по шагам)"),
               ("Режим", mode)]
        t_rows = "".join(
            f"<tr><td>{r['name']}</td><td align='right'>{r['users_mean']:.0f}</td><td align='right'>{r['served_peak']:.0f}/{r['offered_peak']:.0f}</td>"
            f"<td align='right' style='color:{util_color(r['radio_util_peak'])}'>{r['radio_util_peak'] * 100:.0f}%</td>"
            f"<td align='right' style='color:{util_color(r['bh_util_peak'])}'>{r['bh_util_peak'] * 100:.0f}%</td></tr>"
            for r in res.towers_table)
        l_rows = "".join(
            f"<tr><td>{r['label']}</td><td align='right'>{fmt_speed(r['cap'] / 0.94)}</td>"
            f"<td align='right' style='color:{util_color(r['util_peak'])}'>{r['util_peak'] * 100:.0f}%</td>"
            f"<td align='right'>{r['util_mean'] * 100:.0f}%</td></tr>" for r in res.links_table)
        recs = "".join(f"<li>{r}</li>" for r in res.recommendations)
        html = (f"<p style='margin:0 0 6px 0'>{verdict}</p>" + self._kv(kpi)
                + "<p style='margin:8px 0 2px 0'><b>Вышки</b> <span style='color:%s'>(Мбит/с: доставлено/запрошено в пик)</span></p>" % dim
                + "<table cellpadding='2'><tr><th align='left'>Вышка</th><th>Абон.</th><th>Мбит/с</th><th>Радио</th><th>Транспорт</th></tr>"
                + t_rows + "</table>")
        if l_rows:
            html += ("<p style='margin:8px 0 2px 0'><b>Линки</b></p><table cellpadding='2'><tr><th align='left'>Линк</th>"
                     "<th>Скорость</th><th>Пик</th><th>Среднее</th></tr>" + l_rows + "</table>")
        html += f"<p style='margin:8px 0 2px 0'><b>Рекомендации</b></p><ul style='margin-top:0'>{recs}</ul>"
        self.ld_summary.setHtml(html)

    def export_load_report(self):
        res = self._load_result
        if res is None:
            QMessageBox.information(self, "Отчёт", "Сначала запустите тест нагрузки.")
            return
        path = self._save_dialog("Отчёт теста нагрузки", "load_report.csv", "CSV (*.csv)")
        if not path:
            return
        s = res.summary
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n", delimiter=";")
        w.writerow(["Показатель", "Значение"])
        for k, v in s.items():
            w.writerow([k, f"{v:.3f}" if isinstance(v, float) else v])
        w.writerow([])
        w.writerow(["Вышка", "Абонентов (ср.)", "Запрошено пик, Мбит/с", "Доставлено пик, Мбит/с", "Радио, %", "Транспорт, %", "Проблема"])
        for r in res.towers_table:
            w.writerow([r["name"], f"{r['users_mean']:.1f}", f"{r['offered_peak']:.1f}", f"{r['served_peak']:.1f}",
                        f"{r['radio_util_peak'] * 100:.0f}", f"{r['bh_util_peak'] * 100:.0f}", r["transport_issue"]])
        w.writerow([])
        w.writerow(["Линк", "Ёмкость, Мбит/с", "Пик, %", "Среднее, %"])
        for r in res.links_table:
            w.writerow([r["label"], f"{r['cap']:.0f}", f"{r['util_peak'] * 100:.0f}", f"{r['util_mean'] * 100:.0f}"])
        w.writerow([])
        w.writerow(["Шаг", "Активных", "Запрошено, Мбит/с", "Доставлено, Мбит/с", "Задержка ср., мс"])
        for i, st in enumerate(res.steps, 1):
            w.writerow([i, st.active, f"{st.offered_dl:.1f}", f"{st.served_dl:.1f}", f"{st.lat_mean:.1f}"])
        w.writerow([])
        for r in res.recommendations:
            w.writerow(["Рекомендация", r])
        try:
            Path(path).write_text("\ufeff" + buf.getvalue(), encoding="utf-8")
            self.statusBar().showMessage(f"Отчёт сохранён: {path}")
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка записи", str(exc))
