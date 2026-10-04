"""Заглушка PyQt6 для запуска логики gui.py без Qt (в CI/песочнице, где PyQt6 нет).

Проверяет НАШ код (имена, логику, потоки событий), но не корректность вызовов реального Qt API.
"""
import sys
import types

_SIGNALS = {"toggled", "triggered", "clicked", "currentTextChanged", "loadFinished", "itemSelectionChanged",
            "finished", "valueChanged", "accepted", "rejected", "activated", "textChanged"}


class BoundSignal:
    def __init__(self):
        self.slots = []

    def connect(self, f):
        self.slots.append(f)

    def emit(self, *a):
        for f in list(self.slots):
            f(*a)


class pyqtSignal:                                   # noqa: N801
    def __init__(self, *types_):
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault("_sig_" + self.name, BoundSignal())


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        val = Dummy()
        type.__setattr__(cls, name, val)           # одно и то же значение при повторном обращении
        return val


class Dummy(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name in _SIGNALS:
            sig = BoundSignal()
            self.__dict__[name] = sig
            return sig
        d = Dummy()
        self.__dict__[name] = d
        return d

    def __call__(self, *a, **k):
        return Dummy()

    def __or__(self, o):
        return self

    __ror__ = __or__

    def __iter__(self):
        return iter(())


class QThread(Dummy):
    finished = pyqtSignal()

    def start(self):                                # потоки в тесте выполняются синхронно
        self.run()
        self.finished.emit()

    def isRunning(self):
        return False

    def wait(self, *a):
        return True


class QSettings(Dummy):
    _store = {}

    def value(self, key, default=None):
        return self._store.get(key, default)

    def setValue(self, key, val):
        self._store[key] = val


class StatusBar(Dummy):
    last = ""

    def showMessage(self, text, *a):
        self.last = text


_QMAINWINDOW_API = {"setWindowTitle", "resize", "setCentralWidget", "addToolBar", "menuBar", "statusBar",
                    "close", "show", "setStyleSheet"}


class QMainWindow(Dummy):
    """Строгая заглушка: обращение к ещё не созданному атрибуту self.xxx — AttributeError, как в настоящем Qt."""

    def __getattr__(self, name):
        if name in _QMAINWINDOW_API:
            return Dummy()
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def closeEvent(self, event):
        pass

    def statusBar(self):
        return self.__dict__.setdefault("_sb", StatusBar())


class _Text(Dummy):
    text = ""

    def setHtml(self, t):
        self.text = t

    setText = setHtml


QTextBrowser = QLabel = _Text


class QComboBox(Dummy):
    currentTextChanged = pyqtSignal()

    def __init__(self, *a):
        self.items, self.cur = [], ""

    def addItems(self, items):
        for i in list(items):
            self.addItem(i)

    def addItem(self, t):
        self.items.append(t)
        self.cur = self.cur or t

    def clear(self):
        self.items, self.cur = [], ""

    def currentText(self):
        return self.cur

    def setCurrentText(self, t):
        self.cur = t
        self.currentTextChanged.emit(t)

    def findText(self, t):
        return self.items.index(t) if t in self.items else -1


class QCheckBox(Dummy):
    toggled = pyqtSignal()

    def __init__(self, *a):
        self.on = False

    def isChecked(self):
        return self.on

    def setChecked(self, v):
        if v != self.on:
            self.on = v
            self.toggled.emit(v)


class QActionGroup(Dummy):
    def __init__(self, *a):
        self.acts = []

    def addAction(self, act):
        act.group = self
        self.acts.append(act)


class QAction(QCheckBox):
    triggered = pyqtSignal()

    def __init__(self, *a):
        super().__init__()
        self.enabled = True
        self.checkable = False
        self.group = None

    def setChecked(self, v):
        if v and self.group is not None:                 # эксклюзивная группа, как в настоящем Qt
            for other in self.group.acts:
                if other is not self and other.on:
                    other.on = False
                    other.toggled.emit(False)
        super().setChecked(v)

    def setCheckable(self, v):
        self.checkable = v

    def setEnabled(self, v):
        self.enabled = v

    def isEnabled(self):
        return self.enabled


class QSpinBox(Dummy):
    def value(self):
        return 10


class QTableWidget(Dummy):
    itemSelectionChanged = pyqtSignal()

    def __init__(self, *a):
        self.rows, self.sel = 0, None

    def rowCount(self):
        return self.rows

    def setRowCount(self, n):
        self.rows = n

    def selectRow(self, r):
        self.sel = r

    def clearSelection(self):
        self.sel = None

    def selectionModel(self):
        t = self

        class SM:
            def selectedRows(self_):
                class R:
                    def row(s):
                        return t.sel
                return [R()] if t.sel is not None else []
        return SM()


class QMessageBox(Dummy):
    calls = []
    answer = None

    @classmethod
    def _rec(cls, kind, *a):
        cls.calls.append((kind, a))

    @staticmethod
    def information(*a):
        QMessageBox.calls.append(("information", a))

    @staticmethod
    def warning(*a):
        QMessageBox.calls.append(("warning", a))

    @staticmethod
    def about(*a):
        QMessageBox.calls.append(("about", a))

    @staticmethod
    def question(*a):
        QMessageBox.calls.append(("question", a))
        return QMessageBox.StandardButton.Yes


class _Page(Dummy):
    def __init__(self, *a):
        self.js = []

    def runJavaScript(self, code):
        self.js.append(code)

    def profile(self):
        return Dummy()


class QWebEngineView(Dummy):
    loadFinished = pyqtSignal()

    def __init__(self, *a):
        self._page = _Page()
        self.html = None

    def page(self):
        return self._page

    def setPage(self, p):
        self._page = p

    def setHtml(self, html, url=None):
        self.html = html


class QWebEnginePage(Dummy):
    def __init__(self, *a):
        self.js = []

    def runJavaScript(self, code):
        self.js.append(code)


def install():
    names = {
        "PyQt6": {},
        "PyQt6.QtCore": {"QSettings": QSettings, "QThread": QThread, "pyqtSignal": pyqtSignal},
        "PyQt6.QtGui": {"QAction": QAction, "QActionGroup": QActionGroup},
        "PyQt6.QtSvg": {},
        "PyQt6.QtWebEngineCore": {"QWebEnginePage": QWebEnginePage},
        "PyQt6.QtWebEngineWidgets": {"QWebEngineView": QWebEngineView},
        "PyQt6.QtWidgets": {"QMainWindow": QMainWindow, "QLabel": QLabel, "QTextBrowser": QTextBrowser,
                            "QComboBox": QComboBox, "QCheckBox": QCheckBox, "QSpinBox": QSpinBox,
                            "QTableWidget": QTableWidget, "QMessageBox": QMessageBox},
        "PyQt6.QtGui.__acts": {"QAction": QAction},
    }

    class Mod(types.ModuleType):
        def __init__(self, name, own):
            super().__init__(name)
            self._own = own

        def __getattr__(self, n):
            if n.startswith("__"):
                raise AttributeError(n)
            if n in self._own:
                return self._own[n]
            if n == "QAction":
                return QAction
            cls = type(n, (Dummy,), {})
            setattr(self, n, cls)
            return cls

    for name, own in names.items():
        if name.endswith("__acts"):
            continue
        sys.modules[name] = Mod(name, own)
