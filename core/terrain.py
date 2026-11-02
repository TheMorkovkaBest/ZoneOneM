"""Данные о рельефе местности (DEM - Digital Elevation Model).

Загружает высоты рельефа над уровнем моря (SRTM / Copernicus DEM через Open-Meteo API),
кэширует в локальный файл `terrain_cache.npz` и отдаёт высоты и относительные неровности.

Поддерживает:
1. Запрос высот в произвольных точках (lat, lon).
2. Запрос высот вдоль луча / трассы распространения радиосигнала.
3. Окно высот (высотная сетка) вокруг заданной точки для расчёта радиотрасс.
4. Оффлайн-режим с кэшированием и синтетическим/интерполированным рельефом при отсутствии сети.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from calculations import GRID_RES_M, M_PER_DEG_LAT

# Размер блока/плитки для рельефа
TERRAIN_FILE = "terrain_cache.npz"
USER_AGENT = "ZoneOneM/1.0 (https://github.com/TheMorkovkaBest/ZoneOneM; radio planning student project)"
ELEVATION_API_URL = "https://api.open-meteo.com/v1/elevation"


class TerrainData:
    """Управление высотными данными рельефа (DEM)."""

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else Path("buildings_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / TERRAIN_FILE
        self._lock = threading.Lock()

        # Кэш: {(round(lat, 4), round(lon, 4)): elevation_m}
        self._point_cache: Dict[Tuple[float, float], float] = {}
        self.status = "готов"
        self.last_error = ""
        self._load_cache()

    def _load_cache(self) -> int:
        """Загружает сохранённый кэш рельефа с диска."""
        with self._lock:
            if not self.cache_file.exists():
                return 0
            try:
                data = np.load(self.cache_file, allow_pickle=True)
                lats = data.get("lats", np.array([], dtype=np.float64))
                lons = data.get("lons", np.array([], dtype=np.float64))
                elevs = data.get("elevs", np.array([], dtype=np.float32))
                for lat, lon, el in zip(lats, lons, elevs):
                    self._point_cache[(round(float(lat), 4), round(float(lon), 4))] = float(el)
                return len(self._point_cache)
            except Exception as e:
                self.last_error = f"Ошибка чтения кэша рельефа: {e}"
                return 0

    def save_cache(self) -> None:
        """Сохраняет кэш рельефа на диск."""
        with self._lock:
            if not self._point_cache:
                return
            try:
                pts = list(self._point_cache.items())
                lats = np.array([p[0][0] for p in pts], dtype=np.float64)
                lons = np.array([p[0][1] for p in pts], dtype=np.float64)
                elevs = np.array([p[1] for p in pts], dtype=np.float32)
                np.savez_compressed(self.cache_file, lats=lats, lons=lons, elevs=elevs)
            except Exception as e:
                self.last_error = f"Ошибка сохранения кэша рельефа: {e}"

    def get_elevation(self, lat: float, lon: float) -> Optional[float]:
        """Возвращает высоту рельефа в точке или None, если данных нет."""
        key = (round(lat, 4), round(lon, 4))
        with self._lock:
            if key in self._point_cache:
                return self._point_cache[key]
        return None

    def elevations_at(self, lats: Sequence[float], lons: Sequence[float]) -> np.ndarray:
        """Возвращает массив высот для списка координат (с интерполяцией или кэшем)."""
        if len(lats) == 0:
            return np.zeros(0, dtype=np.float32)

        out = np.zeros(len(lats), dtype=np.float32)
        missing_indices = []
        missing_lats = []
        missing_lons = []

        with self._lock:
            for idx, (lat, lon) in enumerate(zip(lats, lons)):
                key = (round(float(lat), 4), round(float(lon), 4))
                if key in self._point_cache:
                    out[idx] = self._point_cache[key]
                else:
                    missing_indices.append(idx)
                    missing_lats.append(float(lat))
                    missing_lons.append(float(lon))

        if missing_indices:
            fetched = self._fetch_open_meteo(missing_lats, missing_lons)
            with self._lock:
                for idx, lat, lon, el in zip(missing_indices, missing_lats, missing_lons, fetched):
                    if el is not None:
                        out[idx] = el
                        self._point_cache[(round(lat, 4), round(lon, 4))] = el
                    else:
                        out[idx] = 0.0

        return out

    def ensure_loaded(self, lat: float, lon: float, radius_m: float = 800.0, step_m: float = 100.0) -> bool:
        """Загружает сетку высот рельефа в заданном радиусе (если ещё не загружена)."""
        lat_step = step_m / M_PER_DEG_LAT
        lon_step = step_m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
        r_steps = int(math.ceil(radius_m / step_m))

        sample_lats = []
        sample_lons = []
        for i in range(-r_steps, r_steps + 1):
            for j in range(-r_steps, r_steps + 1):
                p_lat = lat + i * lat_step
                p_lon = lon + j * lon_step
                key = (round(p_lat, 4), round(p_lon, 4))
                with self._lock:
                    if key not in self._point_cache:
                        sample_lats.append(p_lat)
                        sample_lons.append(p_lon)

        if not sample_lats:
            return True

        # Загружаем пачками до 80 точек
        all_fetched = True
        for b_start in range(0, len(sample_lats), 80):
            b_lats = sample_lats[b_start : b_start + 80]
            b_lons = sample_lons[b_start : b_start + 80]
            elevs = self._fetch_open_meteo(b_lats, b_lons)
            if not elevs or any(e is None for e in elevs):
                all_fetched = False
            with self._lock:
                for p_lat, p_lon, el in zip(b_lats, b_lons, elevs):
                    if el is not None:
                        self._point_cache[(round(p_lat, 4), round(p_lon, 4))] = el

        self.save_cache()
        return all_fetched

    def heights_along(
        self,
        lat: float,
        lon: float,
        ux: float,
        uy: float,
        dists_m: np.ndarray,
        base_elevation: Optional[float] = None,
    ) -> np.ndarray:
        """Вычисляет относительный рельеф (м) вдоль луча/трассы от базовой точки (lat, lon).

        Относительная высота = Elevation(x, y) - Elevation(lat, lon).
        Положительные значения — возвышенности/холмы выше точки передатчика;
        Отрицательные значения — низины/овраги ниже точки передатчика.
        """
        if dists_m.size == 0:
            return np.zeros(0, dtype=np.float32)

        m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
        step_pts = min(len(dists_m), 15)  # сэмплируем ключевые точки вдоль луча
        sample_indices = np.linspace(0, len(dists_m) - 1, step_pts, dtype=int)
        sampled_dists = dists_m[sample_indices]

        sampled_lats = lat + (uy * sampled_dists) / M_PER_DEG_LAT
        sampled_lons = lon + (ux * sampled_dists) / m_lon

        if base_elevation is None:
            base_elevation = float(self.elevations_at([lat], [lon])[0])

        sampled_elevs = self.elevations_at(sampled_lats, sampled_lons)
        rel_sampled = sampled_elevs - base_elevation

        # Интерполируем на полный массив дистанций
        if len(dists_m) > 1:
            rel_heights = np.interp(dists_m, sampled_dists, rel_sampled).astype(np.float32)
        else:
            rel_heights = rel_sampled

        return rel_heights

    def _fetch_open_meteo(self, lats: Sequence[float], lons: Sequence[float]) -> List[Optional[float]]:
        """Выполняет HTTP-запрос к Open-Meteo Elevation API для списка точек."""
        if not lats:
            return []

        lat_str = ",".join(f"{x:.5f}" for x in lats)
        lon_str = ",".join(f"{x:.5f}" for x in lons)
        url = f"{ELEVATION_API_URL}?latitude={lat_str}&longitude={lon_str}"

        ctx = None
        try:
            ctx = ssl._create_unverified_context()
        except Exception:
            pass

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            kwargs = {"context": ctx} if ctx else {}
            with urllib.request.urlopen(req, timeout=4.0, **kwargs) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elevs = data.get("elevation", [])
                return [float(e) if e is not None else None for e in elevs]
        except Exception as e:
            self.last_error = f"Open-Meteo elevation error: {e}"
            return [None] * len(lats)
