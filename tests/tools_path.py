"""Импорт tools/make_demo.py как модуля для тестов."""
import importlib.util
import os

_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "make_demo.py")
_spec = importlib.util.spec_from_file_location("make_demo", _p)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_demo = _mod.build
