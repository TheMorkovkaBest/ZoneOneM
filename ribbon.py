"""Лента инструментов: кнопки сгруппированы по смыслу, у каждой группы — подпись снизу."""
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget


class RibbonGroup(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbonGroup")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 1)
        lay.setSpacing(1)
        self.row = QHBoxLayout()
        self.row.setSpacing(1)
        lay.addLayout(self.row)
        cap = QLabel(title)
        cap.setProperty("role", "ribbon")
        cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(cap)

    def add_action(self, act, menu=None):
        """Кнопка с иконкой и всплывающей документацией; menu — выпадающий список рядом (split-кнопка)."""
        btn = QToolButton()
        btn.setDefaultAction(act)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        btn.setIconSize(QSize(24, 24))
        btn.setProperty("ribbon", True)
        btn.setAutoRaise(True)
        if act.toolTip():
            btn.setToolTip(act.toolTip())
        if menu is not None:
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.row.addWidget(btn)
        return btn

    def add_widget(self, w):
        self.row.addWidget(w)
        return w


class RibbonBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 0, 4, 0)
        self._lay.setSpacing(0)
        self.groups = []

    def add_group(self, title):
        g = RibbonGroup(title)
        self._lay.addWidget(g)
        self.groups.append(g)
        return g

    def add_stretch(self):
        self._lay.addStretch(1)
