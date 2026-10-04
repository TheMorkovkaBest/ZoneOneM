"""Данные о зданиях: загрузка из OpenStreetMap (Overpass) и растр высот.

Главная идея скорости: полигоны зданий растеризуются в растр высот ОДИН раз
(при загрузке, в фоновом потоке) и хранятся плиткам 256×256 ячеек (uint8, метры).
Расчёт вышки потом лишь «вырезает» окно из растра — это занимает доли миллисекунды,
а не пересобирает сетку по каждому зданию, как раньше.
"""
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

import numpy as np

try:
    import requests
except ImportError:          # оффлайн-режим / тесты без requests
    requests = None

from calculations import GRID_RES_M, M_PER_DEG_LAT

TILE = 256
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
FAIL_COOLDOWN_S = 30.0
DEFAULT_HEIGHT_M = 15.0
# Высоты по типу здания, если в OSM нет ни height, ни building:levels.
TYPE_HEIGHTS = {
    "house": 6.0, "detached": 6.0, "semidetached_house": 6.0, "terrace": 7.0, "bungalow": 4.0,
    "garage": 3.0, "garages": 3.0, "shed": 3.0, "hut": 3.0, "cabin": 4.0, "roof": 4.0,
    "carport": 3.0, "kiosk": 3.0, "greenhouse": 4.0, "service": 4.0,
    "apartments": 18.0, "dormitory": 18.0, "hotel": 18.0, "office": 18.0,
    "industrial": 10.0, "warehouse": 8.0, "retail": 8.0, "supermarket": 8.0, "church": 15.0,
}
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _first_number(text):
    m = _NUM_RE.search(str(text))
    return float(m.group(0).replace(",", ".")) if m else None


def parse_height(tags):
    """Высота здания в метрах из тегов OSM (height > building:levels > тип > 15 м)."""
    h = tags.get("height")
    if h is not None:
        val = _first_number(h)
        if val is not None and val > 0:
            return val * 0.3048 if re.search(r"\b(ft|feet|')", str(h)) else val
    lv = tags.get("building:levels")
    if lv is not None:
        val = _first_number(lv)
        if val is not None and val > 0:
            return val * 3.0
    return TYPE_HEIGHTS.get(tags.get("building", ""), DEFAULT_HEIGHT_M)


class BuildingData:
    def __init__(self, cache_dir=None):
        self.buildings = []           # список dict(id, height, poly, bbox) — для совместимости
        self.loaded_centers = []      # [(lat, lon, radius_m)]
        self.status = "нет данных"
        self.last_error = None
        self._ids = set()
        self._lock = threading.RLock()
        self._origin = None           # (lat0, lon0) начало растровой системы координат
        self._tiles = {}              # (ty, tx) -> uint8[TILE, TILE]
        self._fail_until = 0.0
        self.cache_dir = Path(cache_dir) if cache_dir else None

    # ------------------------------------------------------------------ #
    #  Загрузка
    # ------------------------------------------------------------------ #
    def is_covered(self, lat, lon, need_m=400.0):
        """Есть ли уже загруженные данные, покрывающие круг радиусом need_m."""
        with self._lock:
            for c_lat, c_lon, r in self.loaded_centers:
                dist = math.hypot((lat - c_lat) * M_PER_DEG_LAT,
                                  (lon - c_lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
                if dist + need_m <= r:
                    return True
        return False

    def ensure_loaded(self, lat, lon, need_m=600.0, radius_m=1000):
        """Гарантирует наличие зданий вокруг точки. True, если данные есть."""
        if self.is_covered(lat, lon, need_m):
            return True
        return self.fetch(lat, lon, radius_m=max(radius_m, need_m + 400), need_m=need_m)

    def fetch(self, lat, lon, radius_m=1200, need_m=400.0):
        """Скачивает здания вокруг точки. True — успех (или уже загружено)."""
        if self.is_covered(lat, lon, need_m):
            return True
        if requests is None:
            self.last_error = "не установлен модуль requests"
            return False
        if time.time() < self._fail_until:      # недавно была ошибка сети — не долбим сервер
            return False

        query = f"""
        [out:json][timeout:25];
        (
          way["building"](around:{radius_m},{lat},{lon});
          relation["building"](around:{radius_m},{lat},{lon});
        );
        out body;
        >;
        out skel qt;
        """
        headers = {'User-Agent': 'ZoneM/1.0 (radio planning student project)',
                   'Content-Type': 'text/plain; charset=utf-8'}
        self.status = "загрузка зданий…"
        for url in OVERPASS_URLS:
            try:
                resp = requests.post(url, data=query.encode('utf-8'), headers=headers, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    self._save_cache(lat, lon, radius_m, data)
                    self._register_area(lat, lon, radius_m)
                    n = self.add_buildings(self._parse_overpass(data))
                    self.status = f"зданий: {len(self.buildings)} (+{n})"
                    self.last_error = None
                    return True
                self.last_error = f"{url}: HTTP {resp.status_code}"
            except Exception as exc:      # сеть/JSON — пробуем следующий сервер
                self.last_error = f"{url}: {exc}"
        self._fail_until = time.time() + FAIL_COOLDOWN_S
        self.status = "нет сети — расчёт без зданий"
        return False

    def _register_area(self, lat, lon, radius_m):
        with self._lock:
            self.loaded_centers.append((lat, lon, float(radius_m)))

    # --- дисковый кэш: перезапуск программы не требует повторной загрузки ---
    def _cache_path(self, lat, lon, radius_m):
        key = hashlib.md5(f"{lat:.5f},{lon:.5f},{radius_m}".encode()).hexdigest()[:10]
        return self.cache_dir / f"overpass_{key}.json"

    def _save_cache(self, lat, lon, radius_m, data):
        if not self.cache_dir:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {"lat": lat, "lon": lon, "radius_m": radius_m, "data": data}
            self._cache_path(lat, lon, radius_m).write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            self.last_error = f"кэш не записан: {exc}"

    def load_cache(self):
        """Читает все сохранённые ответы Overpass. Возвращает число зданий."""
        if not self.cache_dir or not self.cache_dir.is_dir():
            return 0
        total = 0
        for f in sorted(self.cache_dir.glob("overpass_*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                self._register_area(p["lat"], p["lon"], p["radius_m"])
                total += self.add_buildings(self._parse_overpass(p["data"]))
            except Exception as exc:
                self.last_error = f"кэш {f.name}: {exc}"
        if total:
            self.status = f"зданий: {len(self.buildings)} (кэш)"
        return total

    # ------------------------------------------------------------------ #
    #  Разбор Overpass
    # ------------------------------------------------------------------ #
    def _parse_overpass(self, data):
        elements = data.get('elements', [])
        nodes = {e['id']: (e['lat'], e['lon']) for e in elements
                 if e.get('type') == 'node' and 'lat' in e}
        ways = {e['id']: e for e in elements if e.get('type') == 'way' and 'nodes' in e}

        def ring(way):
            return [nodes[n] for n in way['nodes'] if n in nodes]

        out = []
        for w in ways.values():                       # обычные здания
            tags = w.get('tags') or {}
            if 'building' not in tags:
                continue                              # безымянные части relation разбираем ниже
            poly = ring(w)
            if len(poly) >= 3:
                out.append({'id': f"w{w['id']}", 'height': parse_height(tags), 'poly': poly})
        for r in elements:                            # здания-мультиполигоны: внешние контуры
            if r.get('type') != 'relation':
                continue
            tags = r.get('tags') or {}
            if 'building' not in tags:
                continue
            for i, m in enumerate(r.get('members', [])):
                if m.get('type') == 'way' and m.get('role') == 'outer' and m.get('ref') in ways:
                    poly = ring(ways[m['ref']])
                    if len(poly) >= 3:
                        out.append({'id': f"r{r['id']}_{i}", 'height': parse_height(tags), 'poly': poly})
        return out

    # ------------------------------------------------------------------ #
    #  Растр
    # ------------------------------------------------------------------ #
    def add_buildings(self, items):
        """Добавляет здания [{'id', 'height', 'poly': [(lat, lon)...]}], без дублей.

        Возвращает число реально добавленных. Можно вызывать и напрямую (тесты,
        импорт своих данных).
        """
        added = 0
        with self._lock:
            for b in items:
                bid = b.get('id')
                if bid is not None:
                    if bid in self._ids:
                        continue
                    self._ids.add(bid)
                poly = [(float(p[0]), float(p[1])) for p in b['poly']]
                if len(poly) < 3:
                    continue
                if self._origin is None:
                    self._origin = (poly[0][0], poly[0][1])
                lats = [p[0] for p in poly]
                lons = [p[1] for p in poly]
                rec = {'id': bid, 'height': float(b.get('height', DEFAULT_HEIGHT_M)), 'poly': poly,
                       'bbox': (min(lats), max(lats), min(lons), max(lons))}
                self.buildings.append(rec)
                self._rasterize(rec)
                added += 1
        return added

    def _to_cells(self, lat, lon):
        lat0, lon0 = self._origin
        x = (np.asarray(lon, dtype=float) - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)) / GRID_RES_M
        y = (np.asarray(lat, dtype=float) - lat0) * M_PER_DEG_LAT / GRID_RES_M
        return x, y

    def _rasterize(self, b):
        xs, ys = self._to_cells([p[0] for p in b['poly']], [p[1] for p in b['poly']])
        x0, x1 = int(math.floor(xs.min())), int(math.ceil(xs.max()))
        y0, y1 = int(math.floor(ys.min())), int(math.ceil(ys.max()))
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        px = x0 + np.arange(w) + 0.5                          # центры ячеек
        py = y0 + np.arange(h) + 0.5
        ex1, ey1 = xs, ys
        ex2, ey2 = np.roll(xs, -1), np.roll(ys, -1)
        # чёт-нечет сразу по всем рёбрам: пересечения горизонтального луча справа от ячейки
        valid = (ey1[None, :] > py[:, None]) != (ey2[None, :] > py[:, None])        # [h, E]
        dy = np.where(ey2 == ey1, 1.0, ey2 - ey1)
        xc = ex1[None, :] + (py[:, None] - ey1[None, :]) * ((ex2 - ex1) / dy)[None, :]
        crossings = (valid[:, :, None] & (xc[:, :, None] > px[None, None, :])).sum(axis=1)
        mask = (crossings & 1).astype(bool)                   # [h, w]
        if not mask.any():                                    # совсем крошечное здание
            mask = np.zeros((h, w), dtype=bool)
            mask[min(h - 1, int(ys.mean() - y0)), min(w - 1, int(xs.mean() - x0))] = True
        hv = np.uint8(min(255, max(1, round(b['height']))))
        for ty in range(y0 // TILE, (y0 + h - 1) // TILE + 1):
            for tx in range(x0 // TILE, (x0 + w - 1) // TILE + 1):
                gy0, gx0 = ty * TILE, tx * TILE
                ya, yb = max(y0, gy0), min(y0 + h, gy0 + TILE)
                xa, xb = max(x0, gx0), min(x0 + w, gx0 + TILE)
                sub = mask[ya - y0:yb - y0, xa - x0:xb - x0]
                if not sub.any():
                    continue
                tile = self._tiles.get((ty, tx))
                if tile is None:
                    tile = self._tiles[(ty, tx)] = np.zeros((TILE, TILE), dtype=np.uint8)
                view = tile[ya - gy0:yb - gy0, xa - gx0:xb - gx0]
                np.maximum(view, np.where(sub, hv, np.uint8(0)), out=view)

    # ------------------------------------------------------------------ #
    #  Запросы к растру
    # ------------------------------------------------------------------ #
    def occupancy_window(self, lat, lon, half_m, min_height):
        """Окно растра вокруг точки.

        :return: (occ[bool, H×W], px, py) — занятость ячеек зданиями не ниже
                 min_height и положение вышки в ячейках окна; либо None, если в
                 этой области нет ни одной плитки с данными.
        """
        with self._lock:
            if self._origin is None or not self._tiles:
                return None
            cx, cy = self._to_cells(lat, lon)
            cx, cy = float(cx), float(cy)
            half = int(math.ceil(half_m / GRID_RES_M)) + 2
            gx0, gy0 = int(math.floor(cx)) - half, int(math.floor(cy)) - half
            side = 2 * half + 1
            win = None
            for ty in range(gy0 // TILE, (gy0 + side - 1) // TILE + 1):
                for tx in range(gx0 // TILE, (gx0 + side - 1) // TILE + 1):
                    tile = self._tiles.get((ty, tx))
                    if tile is None:
                        continue
                    if win is None:
                        win = np.zeros((side, side), dtype=np.uint8)
                    ya, yb = max(gy0, ty * TILE), min(gy0 + side, ty * TILE + TILE)
                    xa, xb = max(gx0, tx * TILE), min(gx0 + side, tx * TILE + TILE)
                    win[ya - gy0:yb - gy0, xa - gx0:xb - gx0] = \
                        tile[ya - ty * TILE:yb - ty * TILE, xa - tx * TILE:xb - tx * TILE]
        if win is None:
            return None
        return win >= max(1.0, float(min_height)), cx - gx0, cy - gy0

    def heights_along(self, lat, lon, ux, uy, dists_m):
        """Высоты зданий (м, 0 — пусто) вдоль луча из точки; ux/uy — единичный вектор (восток, север)."""
        dists_m = np.asarray(dists_m, dtype=float)
        out = np.zeros(dists_m.shape, dtype=float)
        with self._lock:
            if self._origin is None or not self._tiles or dists_m.size == 0:
                return out
            cx, cy = self._to_cells(lat, lon)
            gx = np.floor(float(cx) + ux * dists_m / GRID_RES_M).astype(np.int64)
            gy = np.floor(float(cy) + uy * dists_m / GRID_RES_M).astype(np.int64)
            tx, ty = gx // TILE, gy // TILE
            for key in set(zip(ty.tolist(), tx.tolist())):
                tile = self._tiles.get(key)
                if tile is None:
                    continue
                m = (ty == key[0]) & (tx == key[1])
                out[m] = tile[gy[m] - key[0] * TILE, gx[m] - key[1] * TILE]
        return out

    def count_in_radius(self, lat, lon, radius_m):
        """Сколько зданий (по bbox) в радиусе — для информации в интерфейсе."""
        with self._lock:
            n = 0
            for b in self.buildings:
                c_lat = (b['bbox'][0] + b['bbox'][1]) / 2
                c_lon = (b['bbox'][2] + b['bbox'][3]) / 2
                d = math.hypot((c_lat - lat) * M_PER_DEG_LAT,
                               (c_lon - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
                n += d <= radius_m
            return n
