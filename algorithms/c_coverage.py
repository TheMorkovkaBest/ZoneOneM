"""Python-обёртка (ctypes) над си-ядром оптимизации покрытия c_coverage.c."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

_LIB: Optional[ctypes.CDLL] = None
_DIR = Path(__file__).resolve().parent


def _compile_c_lib(c_path: Path, out_path: Path) -> bool:
    """Компилирует c_coverage.c в разделяемую библиотеку при первом запуске."""
    compiler = "clang" if sys.platform == "darwin" else "gcc"
    cmd = [compiler, "-O3", "-shared", "-fPIC", str(c_path), "-o", str(out_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False


def get_c_library() -> Optional[ctypes.CDLL]:
    """Загружает или собирает библиотеку libc_coverage."""
    global _LIB
    if _LIB is not None:
        return _LIB

    if sys.platform == "darwin":
        lib_name = "libc_coverage.dylib"
    elif sys.platform == "win32":
        lib_name = "libc_coverage.dll"
    else:
        lib_name = "libc_coverage.so"
    lib_path = _DIR / lib_name
    c_source = _DIR / "c_coverage.c"

    if not lib_path.exists() and c_source.exists():
        _compile_c_lib(c_source, lib_path)

    if not lib_path.exists() and sys.platform != "win32":
        # Попробуем альтернативное расширение (macOS / Linux)
        alt_path = _DIR / ("libc_coverage.so" if sys.platform == "darwin" else "libc_coverage.dylib")
        if alt_path.exists():
            lib_path = alt_path

    if not lib_path.exists():
        return None

    try:
        cdll = ctypes.CDLL(str(lib_path))

        # c_compute_sector_mask signature
        cdll.c_compute_sector_mask.argtypes = [
            ctypes.c_int,                                   # num_tiles
            ctypes.POINTER(ctypes.c_double),                # tile_lats
            ctypes.POINTER(ctypes.c_double),                # tile_lons
            ctypes.c_double,                                # site_lat
            ctypes.c_double,                                # site_lon
            ctypes.c_int,                                   # num_rays
            ctypes.POINTER(ctypes.c_double),                # ray_reaches
            ctypes.POINTER(ctypes.c_uint64),                # out_mask
        ]
        cdll.c_compute_sector_mask.restype = ctypes.c_int

        # c_greedy_set_cover signature
        cdll.c_greedy_set_cover.argtypes = [
            ctypes.c_int,                                   # num_candidates
            ctypes.c_int,                                   # num_tiles
            ctypes.POINTER(ctypes.c_uint64),                # cand_masks
            ctypes.POINTER(ctypes.c_int),                   # site_ids
            ctypes.c_int,                                   # max_towers
            ctypes.c_int,                                   # max_sectors_per_site
            ctypes.c_int,                                   # min_gain_tiles
            ctypes.c_int,                                   # target_covered_count
            ctypes.POINTER(ctypes.c_int),                   # out_selected
        ]
        cdll.c_greedy_set_cover.restype = ctypes.c_int

        _LIB = cdll
        return _LIB
    except Exception as e:
        print(f"Warning: Failed to load C library {lib_path}: {e}")
        return None


def is_c_available() -> bool:
    """Проверяет доступность Си-ядра."""
    return get_c_library() is not None


def c_compute_sector_mask(
    tile_lats: np.ndarray,
    tile_lons: np.ndarray,
    site_lat: float,
    site_lon: float,
    ray_reaches: np.ndarray,
) -> Tuple[np.ndarray, int]:
    """Быстрый расчёт маски видимости сектора в Си.

    :return: (uint64 array маски размером (N + 63) // 64, число покрытых точек)
    """
    lib = get_c_library()
    num_tiles = len(tile_lats)
    if lib is None or num_tiles == 0:
        return np.zeros(0, dtype=np.uint64), 0

    num_words = (num_tiles + 63) // 64
    out_mask = np.zeros(num_words, dtype=np.uint64)

    lats_c = np.ascontiguousarray(tile_lats, dtype=np.float64)
    lons_c = np.ascontiguousarray(tile_lons, dtype=np.float64)
    reaches_c = np.ascontiguousarray(ray_reaches, dtype=np.float64)

    covered = lib.c_compute_sector_mask(
        num_tiles,
        lats_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        lons_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        float(site_lat),
        float(site_lon),
        len(reaches_c),
        reaches_c.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        out_mask.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
    )
    return out_mask, covered


def c_greedy_set_cover(
    cand_masks: np.ndarray,
    site_ids: Sequence[int],
    num_tiles: int,
    max_towers: int,
    max_sectors_per_site: int = 3,
    min_gain_tiles: int = 3,
    target_ratio: float = 0.95,
) -> List[int]:
    """Сверхбыстрый битовый отбор вышек в Си.

    :param cand_masks: массив uint64 формы (C, num_words)
    :param site_ids: список индексов площадок для каждого кандидата
    :return: список отобранных индексов кандидатов
    """
    lib = get_c_library()
    num_candidates = len(cand_masks)
    if lib is None or num_candidates == 0 or num_tiles == 0:
        return []

    target_count = int(num_tiles * target_ratio)
    out_selected = np.zeros(max_towers, dtype=np.int32)

    masks_c = np.ascontiguousarray(cand_masks, dtype=np.uint64)
    sites_c = np.ascontiguousarray(site_ids, dtype=np.int32)

    selected_count = lib.c_greedy_set_cover(
        num_candidates,
        num_tiles,
        masks_c.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
        sites_c.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        int(max_towers),
        int(max_sectors_per_site),
        int(min_gain_tiles),
        target_count,
        out_selected.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
    )

    return [int(out_selected[i]) for i in range(selected_count)]
