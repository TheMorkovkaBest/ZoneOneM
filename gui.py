#!/usr/bin/env python3
"""Точка входа для запуска GUI приложения ZoneOneM (обратная совместимость с `python gui.py`).

Основной код графического интерфейса расположен в пакете `ui/gui.py`.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "catalog", "networking", "algorithms", "ui"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ui.gui import *
from ui.gui import main

if __name__ == "__main__":
    main()
