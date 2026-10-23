"""Оформление интерфейса: тёмная/светлая темы (QSS) и векторные иконки."""
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

try:                                   # QtSvg входит в колесо PyQt6; на всякий случай — запасной вариант
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:                    # pragma: no cover
    QSvgRenderer = None

THEMES = {
    "dark": {
        "bg0": "#10141a", "bg1": "#171c24", "bg2": "#202733", "bg3": "#2a3342",
        "border": "#2b3443", "text": "#e6eaf0", "dim": "#8f9bad",
        "accent": "#3e8bff", "accent_hover": "#5a9eff", "accent_text": "#ffffff",
        "danger": "#e5484d", "success": "#30c26f", "warn": "#f5a524",
    },
    "light": {
        "bg0": "#eef1f5", "bg1": "#ffffff", "bg2": "#f3f5f9", "bg3": "#e3e8ef",
        "border": "#d3dae4", "text": "#1c2430", "dim": "#667487",
        "accent": "#1f6fe0", "accent_hover": "#3b84f0", "accent_text": "#ffffff",
        "danger": "#d63a40", "success": "#1f9d57", "warn": "#c77d0a",
    },
}

# Иконки в стиле Feather (24×24, штрих); внутренний SVG-код.
ICONS = {
    "navigate": '<path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/><path d="M13 13l6 6"/>',
    "tower": '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/>',
    "ruler": '<path d="M3 17L17 3l4 4L7 21z"/><path d="M7 13l2 2M10 10l2 2M13 7l2 2"/>',
    "probe": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m5 0V4a2 2 0 0 1 2-2h0a2 2 0 0 1 2 2v2"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    "goto": '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
    "calc": '<rect x="4" y="2" width="16" height="20" rx="2"/><line x1="8" y1="6" x2="16" y2="6"/><line x1="8" y1="11" x2="8" y2="11"/><line x1="12" y1="11" x2="12" y2="11"/><line x1="16" y1="11" x2="16" y2="11"/><line x1="8" y1="15" x2="8" y2="15"/><line x1="12" y1="15" x2="12" y2="15"/><line x1="16" y1="15" x2="16" y2="15"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "polygon": '<polygon points="12 2 22 9 18 21 6 21 2 9"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    "play": '<polygon points="5 3 19 12 5 21 5 3"/>',
    "camera": '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "server": '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "send": '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "bar": '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "structure": '<line x1="4" y1="21" x2="10" y2="4"/><line x1="20" y1="21" x2="14" y2="4"/><line x1="10" y1="4" x2="14" y2="4"/><line x1="12" y1="1" x2="12" y2="4"/><line x1="8" y1="10" x2="16" y2="10"/><line x1="6" y1="16" x2="18" y2="16"/><line x1="10" y1="4" x2="16" y2="10"/><line x1="14" y1="4" x2="8" y2="10"/><line x1="8" y1="10" x2="18" y2="16"/><line x1="16" y1="10" x2="6" y2="16"/><line x1="6" y1="16" x2="20" y2="21"/><line x1="18" y1="16" x2="4" y2="21"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
}


def _pixmap(name, color, size):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" '
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (color, ICONS[name]))
    px = size * 2                                   # рисуем в 2× для чёткости на HiDPI
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    pm.setDevicePixelRatio(2.0)
    return pm


def make_icon(name, color, color_on=None, size=20):
    """QIcon из встроенного набора; color_on — цвет во включённом (checked) состоянии."""
    icon = QIcon()
    if QSvgRenderer is None or name not in ICONS:
        return icon
    icon.addPixmap(_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_pixmap(name, color_on or color, size), QIcon.Mode.Normal, QIcon.State.On)
    return icon


_QSS = """
* { font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: @bg0; }
QWidget { color: @text; }
QWidget#panel, QScrollArea, QScrollArea > QWidget > QWidget { background: @bg1; }
QLabel { background: transparent; }
QLabel[role="h1"] { font-size: 15px; font-weight: 600; }
QLabel[role="section"] { color: @dim; font-size: 11px; font-weight: 700; padding-top: 6px; }
QLabel[role="dim"] { color: @dim; }

QMenuBar { background: @bg1; border-bottom: 1px solid @border; padding: 2px 4px; }
QMenuBar::item { padding: 5px 10px; border-radius: 5px; background: transparent; }
QMenuBar::item:selected { background: @bg3; }
QMenu { background: @bg1; border: 1px solid @border; padding: 5px; border-radius: 8px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: @accent; color: @accent_text; }
QMenu::separator { height: 1px; background: @border; margin: 5px 6px; }

QToolBar { background: @bg1; border: none; border-bottom: 1px solid @border; padding: 5px 8px; spacing: 3px; }
QToolBar::separator { background: @border; width: 1px; margin: 5px 8px; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 6px 10px; }
QToolButton:hover { background: @bg3; }
QToolButton:pressed { background: @bg2; }
QToolButton:checked { background: @accent; color: @accent_text; }
QToolButton:disabled { color: @dim; }
QToolButton::menu-indicator { image: none; width: 0px; }
QSlider::groove:horizontal { height: 4px; background: @bg3; border-radius: 2px; }
QSlider::handle:horizontal { background: @accent; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QTreeWidget { background: @bg1; border: 1px solid @border; border-radius: 8px; outline: none;
    selection-background-color: @accent; selection-color: @accent_text; }
QTreeWidget::item { padding: 3px 2px; }
QPlainTextEdit { font-family: "Consolas", "Cascadia Mono", monospace; }
QToolButton[ribbon="true"] { padding: 4px 6px; min-width: 32px; min-height: 32px; border-radius: 6px; }
QToolButton[ribbon="true"]::menu-button { border: none; width: 12px; }
QFrame#ribbonGroup { border: none; border-right: 1px solid @border; background: transparent; padding: 0 4px; }
QLabel[role="ribbon"] { color: @dim; font-size: 10px; font-weight: 600; padding-top: 2px; }
QToolButton[role="zone"] { background: @bg2; border: 1px solid @border; padding: 7px 10px; text-align: left; }
QToolButton[role="zone"]:hover { border-color: @accent; }
QToolButton[role="zone"]:checked { background: @accent; border-color: @accent; }

QPushButton { background: @bg3; border: 1px solid @border; border-radius: 7px; padding: 7px 14px; }
QPushButton:hover { border-color: @accent; }
QPushButton:pressed { background: @bg2; }
QPushButton:disabled { color: @dim; background: @bg2; }
QPushButton[primary="true"] { background: @accent; border-color: @accent; color: @accent_text; font-weight: 600; }
QPushButton[primary="true"]:hover { background: @accent_hover; }
QPushButton[danger="true"] { color: @danger; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
    background: @bg2; border: 1px solid @border; border-radius: 6px; padding: 5px 8px;
    selection-background-color: @accent; selection-color: @accent_text; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @accent; }
QComboBox QAbstractItemView { background: @bg1; border: 1px solid @border; selection-background-color: @accent;
    selection-color: @accent_text; outline: none; }
QCheckBox { spacing: 8px; background: transparent; }

QTextBrowser, QTextEdit { background: @bg1; border: 1px solid @border; border-radius: 8px; padding: 6px; }
QGroupBox { border: 1px solid @border; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: @dim; font-weight: 600; }

QTableWidget { background: @bg1; border: 1px solid @border; border-radius: 8px; gridline-color: transparent;
    alternate-background-color: @bg2; selection-background-color: @accent; selection-color: @accent_text; }
QTableWidget::item { padding: 4px 6px; border: none; }
QHeaderView::section { background: @bg2; color: @dim; border: none; border-bottom: 1px solid @border;
    padding: 6px 8px; font-weight: 600; }
QTableCornerButton::section { background: @bg2; border: none; }

QTabWidget::pane { border: 1px solid @border; border-radius: 8px; top: -1px; background: @bg1; }
QTabBar::tab { background: transparent; color: @dim; padding: 7px 14px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @text; border-bottom: 2px solid @accent; }
QTabBar::tab:hover { color: @text; }

QSplitter::handle { background: @border; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @bg3; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: @dim; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: @bg3; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: @bg1; border-top: 1px solid @border; color: @dim; }
QStatusBar::item { border: none; }
QToolTip { background: @bg3; color: @text; border: 1px solid @border; padding: 4px 8px; }
QProgressBar { background: @bg2; border: none; border-radius: 4px; height: 6px; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 4px; }
"""


def build_qss(theme_name):
    t = THEMES[theme_name]
    qss = _QSS
    for key in sorted(t, key=len, reverse=True):        # длинные ключи первыми (accent_hover до accent)
        qss = qss.replace("@" + key, t[key])
    return qss
