"""Данные о зданиях: загрузка из OpenStreetMap (Overpass) и растр высот.

Главная идея скорости: полигоны зданий растеризуются в растр высот ОДИН раз
(при загрузке, в фоновом потоке) и хранятся плиткам 256×256 ячеек (uint8, метры).
Расчёт вышки потом лишь «вырезает» окно из растра — это занимает доли миллисекунды,
а не пересобирает сетку по каждому зданию, как раньше.
"""
import hashlib
import json
import math
import queue
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
# Публичные серверы Overpass часто отвечают 502/504 или по 20–35 с, поэтому опрашиваются
# параллельно (по OVERPASS_PARALLEL), а первым идёт сервер, ответивший успешно в прошлый раз.
OVERPASS_URLS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
OVERPASS_PARALLEL = 3
HTTP_TIMEOUT = (4.0, 30.0)       # (соединение, чтение), с
FAIL_COOLDOWN_S = 20.0
USER_AGENT = "ZoneOneM/1.0 (https://github.com/TheMorkovkaBest/ZoneOneM; radio planning student project)"
RASTER_CHUNK = 200               # зданий за один захват блокировки (расчёт вышки не ждёт всю пачку)
RASTER_FILE = "raster_cache.npz"
RASTER_VERSION = 1
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


def _urllib_download(url, query, headers):
    """Запасной загрузчик из стандартной библиотеки Python (не требует requests).

    Поддерживает системные прокси Windows, SSL с обходом проблемных сертификатов
    (например, перехват антивирусом Касперского/ESET) и gzip-распаковку.
    """
    import gzip
    import ssl
    import urllib.parse
    import urllib.request

    contexts = []
    try:
        contexts.append(ssl.create_default_context())
    except Exception:
        pass
    try:
        contexts.append(ssl._create_unverified_context())
    except Exception:
        pass
    if not contexts:
        contexts.append(None)

    encoded_data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err = None

    for ctx in contexts:
        # 1. Попытка POST
        try:
            req = urllib.request.Request(url, data=encoded_data, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT[0] + min(20.0, HTTP_TIMEOUT[1]), context=ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or (len(raw) >= 2 and raw[:2] == b"\x1f\x8b"):
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", errors="replace"))
                remark = str(data.get("remark", ""))
                if "error" in remark.lower():
                    raise RuntimeError(remark[:120])
                return data
        except Exception as exc:
            last_err = exc

        # 2. Попытка GET (если сервер или прокси блокирует POST)
        try:
            get_url = f"{url}?{urllib.parse.urlencode({'data': query})}"
            req = urllib.request.Request(get_url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT[0] + min(20.0, HTTP_TIMEOUT[1]), context=ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or (len(raw) >= 2 and raw[:2] == b"\x1f\x8b"):
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", errors="replace"))
                remark = str(data.get("remark", ""))
                if "error" in remark.lower():
                    raise RuntimeError(remark[:120])
                return data
        except Exception as exc2:
            last_err = exc2
            continue

    if last_err:
        raise last_err
    raise RuntimeError("Не удалось получить ответ через urllib")


def _download_from_url(url, query, headers):
    """Скачивание с одного URL с автоматическим выбором requests / urllib и обходом SSL-ошибок Windows."""
    if requests is not None:
        try:
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                remark = str(data.get("remark", ""))
                if "error" in remark.lower():
                    raise RuntimeError(remark[:120])
                return data
            elif resp.status_code in (403, 405):
                resp = requests.get(url, params={"data": query}, headers=headers, timeout=HTTP_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    remark = str(data.get("remark", ""))
                    if "error" in remark.lower():
                        raise RuntimeError(remark[:120])
                    return data
            raise RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:
            ssl_err_cls = getattr(getattr(requests, "exceptions", None), "SSLError", ())
            proxy_err_cls = getattr(getattr(requests, "exceptions", None), "ProxyError", ())
            if isinstance(exc, (ssl_err_cls, proxy_err_cls)):
                try:
                    resp = requests.post(url, data={"data": query}, headers=headers, timeout=HTTP_TIMEOUT, verify=False)
                    if resp.status_code == 200:
                        data = resp.json()
                        remark = str(data.get("remark", ""))
                        if "error" in remark.lower():
                            raise RuntimeError(remark[:120])
                        return data
                except Exception:
                    pass
                # Запасной путь через urllib только для обхода специфичных SSL/прокси ошибок requests на Windows
                try:
                    return _urllib_download(url, query, headers)
                except Exception:
                    raise exc
            raise exc

    return _urllib_download(url, query, headers)


class BuildingData:
    def __init__(self, cache_dir=None):
        self.buildings = []           # список dict(id, height, poly, bbox) — для совместимости
        self.loaded_centers = []      # [(lat, lon, radius_m)]
        self.status = "нет данных"
        self.last_error = None
        self._ids = set()
        self._lock = threading.RLock()
        self._fetch_lock = threading.Lock()   # одна загрузка за раз: второй поток ждёт и берёт её результат
        self._origin = None           # (lat0, lon0) начало растровой системы координат
        self._tiles = {}              # (ty, tx) -> uint8[TILE, TILE]
        self._fail_until = 0.0
        self._good_url = None         # сервер, ответивший успешно последним
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._last_win_key = None
        self._last_win = None

    # ------------------------------------------------------------------ #
    #  Загрузка
    # ------------------------------------------------------------------ #
    def reset_cooldown(self):
        """Сброс паузы после ошибки сети, чтобы повторная попытка (например, по F5) сработала немедленно."""
        self._fail_until = 0.0
        self.last_error = None

    def is_covered(self, lat, lon, need_m=400.0):
        """Есть ли уже загруженные данные, покрывающие область точки."""
        with self._lock:
            if self._origin is not None and self._tiles:
                cx, cy = self._to_cells(lat, lon)
                tx, ty = int(math.floor(cx)) // TILE, int(math.floor(cy)) // TILE
                if (ty, tx) in self._tiles:
                    return True
            for c_lat, c_lon, r in self.loaded_centers:
                dist = math.hypot((lat - c_lat) * M_PER_DEG_LAT,
                                  (lon - c_lon) * M_PER_DEG_LAT * math.cos(math.radians(lat)))
                if dist <= r:
                    return True
        return False

    def ensure_loaded(self, lat, lon, need_m=600.0, radius_m=900, cancel=None):
        """Гарантирует наличие зданий вокруг точки. True, если данные есть."""
        if self.is_covered(lat, lon, need_m):
            return True
        return self.fetch(lat, lon, radius_m=max(radius_m, need_m + 150), need_m=need_m, cancel=cancel)

    def fetch(self, lat, lon, radius_m=900, need_m=400.0, cancel=None):
        """Скачивает здания вокруг точки. True — успех (или уже загружено).

        cancel — необязательная функция без аргументов; вернула True — загрузка прерывается.
        """
        if self.is_covered(lat, lon, need_m):
            return True
        with self._fetch_lock:
            # пока ждали блокировку, другой поток мог скачать этот же район — второй запрос не нужен
            if self.is_covered(lat, lon, need_m):
                return True
            if time.time() < self._fail_until:      # недавно была ошибка сети — не долбим сервер
                return False
            self.status = "загрузка зданий…"
            data = self._download(self._query(lat, lon, radius_m), cancel)
            if data is None:
                if not (cancel and cancel()):
                    self._fail_until = time.time() + FAIL_COOLDOWN_S
                    self.status = "нет сети — расчёт без зданий"
                return False
            self._save_cache(lat, lon, radius_m, data)
            n = self.add_buildings(self._parse_overpass(data))
            self._register_area(lat, lon, radius_m)       # только после растеризации: иначе расчёт возьмёт пустой растр
            self.status = f"зданий: {self.n_buildings} (+{n})"
            self.last_error = None
            self.save_raster()
            return True

    @staticmethod
    def _query(lat, lon, radius_m):
        return (f'[out:json][timeout:60];'
                f'(way["building"](around:{radius_m:.0f},{lat:.6f},{lon:.6f});'
                f'relation["building"](around:{radius_m:.0f},{lat:.6f},{lon:.6f}););'
                f'out body;>;out skel qt;')

    def _download(self, query, cancel=None):
        """Запрос к серверам Overpass: по OVERPASS_PARALLEL одновременно, побеждает первый успешный ответ."""
        urls = sorted(OVERPASS_URLS, key=lambda u: u != self._good_url)
        results = queue.Queue()
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
        }

        def worker(url):
            try:
                data = _download_from_url(url, query, headers)
                results.put((url, data, None))
            except Exception as exc:             # сеть/JSON — пробуем следующий сервер
                results.put((url, None, exc))

        pending, running = list(urls), 0
        while pending and running < OVERPASS_PARALLEL:
            threading.Thread(target=worker, args=(pending.pop(0),), daemon=True).start()
            running += 1
        errors = []
        while running:
            try:
                url, data, exc = results.get(timeout=0.25)
            except queue.Empty:
                if cancel and cancel():
                    return None                   # потоки-демоны доработают сами, их ответ никому не нужен
                continue
            running -= 1
            if data is not None:
                self._good_url = url
                return data
            errors.append(f"{url}: {exc}")
            if pending:
                threading.Thread(target=worker, args=(pending.pop(0),), daemon=True).start()
                running += 1
        self.last_error = "; ".join(errors)
        return None

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

    def _sources(self):
        """Сохранённые ответы Overpass: [(имя файла, размер)] — по ним проверяется актуальность растра."""
        return sorted((f.name, f.stat().st_size) for f in self.cache_dir.glob("overpass_*.json"))

    def load_cache(self):
        """Восстанавливает здания с диска. Возвращает число зданий.

        Быстрый путь — готовый растр (raster_cache.npz, миллисекунды). Медленный — разбор всех
        ответов Overpass (секунды и постоянный захват GIL фоновым потоком: интерфейс подтормаживал
        при каждом запуске); после него растр сохраняется, и следующий запуск снова быстрый.
        """
        if not self.cache_dir or not self.cache_dir.is_dir():
            return 0
        sources = self._sources()
        if sources and self._load_raster(sources):
            self.status = f"зданий: {self.n_buildings} (кэш)"
            return self.n_buildings
        total = 0
        for f in sorted(self.cache_dir.glob("overpass_*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                total += self.add_buildings(self._parse_overpass(p["data"]))
                self._register_area(p["lat"], p["lon"], p["radius_m"])
            except Exception as exc:
                self.last_error = f"кэш {f.name}: {exc}"
        if total:
            self.status = f"зданий: {self.n_buildings} (кэш)"
            self.save_raster()
        return total

    @property
    def n_buildings(self):
        return len(self._ids) if self._ids else len(self.buildings)

    def _raster_path(self):
        return self.cache_dir / RASTER_FILE

    def save_raster(self):
        """Снимок растра на диск (атомарно: временный файл + переименование)."""
        if not self.cache_dir:
            return
        try:
            with self._lock:
                if self._origin is None or not self._tiles:
                    return
                keys = np.array(list(self._tiles.keys()), dtype=np.int32).reshape(-1, 2)
                tiles = np.stack(list(self._tiles.values()))
                meta = {"version": RASTER_VERSION, "origin": list(self._origin), "centers": self.loaded_centers,
                        "ids": [i for i in self._ids if isinstance(i, (str, int))], "sources": self._sources()}
            tmp = self._raster_path().with_suffix(".tmp.npz")
            np.savez_compressed(tmp, keys=keys, tiles=tiles, meta=np.array(json.dumps(meta)))
            for attempt in range(5):
                try:
                    tmp.replace(self._raster_path())
                    break
                except OSError:
                    # На Windows файл может быть кратковременно заблокирован антивирусом
                    time.sleep(0.05 * (attempt + 1))
            else:
                tmp.replace(self._raster_path())
        except Exception as exc:
            self.last_error = f"растр не сохранён: {exc}"

    def _load_raster(self, sources):
        try:
            with np.load(self._raster_path()) as z:
                meta = json.loads(str(z["meta"]))
                cached_sources = meta.get("sources", [])
                source_names = {s[0] for s in sources}
                cached_names = {s[0] for s in cached_sources}
                # На Windows при git-клонировании с core.autocrlf размер overpass_*.json меняется из-за перевода строк.
                # Сравниваем состав файлов по именам.
                if meta.get("version") != RASTER_VERSION or source_names != cached_names:
                    return False                      # появились/изменились ответы Overpass — пересобрать
                keys, tiles = z["keys"], z["tiles"]
        except (OSError, KeyError, ValueError):
            return False
        with self._lock:
            if self._tiles:                           # растр уже не пуст (данные пришли раньше) — не смешиваем
                return False
            self._origin = tuple(meta["origin"])
            self._tiles = {(int(k[0]), int(k[1])): tiles[i].copy() for i, k in enumerate(keys)}
            self._ids = set(meta["ids"])
            self.loaded_centers = [tuple(c) for c in meta["centers"]]
        return True

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
        items = list(items)
        with self._lock:
            self._last_win_key = None
            self._last_win = None
        for start in range(0, len(items), RASTER_CHUNK):
            recs = []
            with self._lock:
                for b in items[start:start + RASTER_CHUNK]:
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
                    recs.append({'id': bid, 'height': float(b.get('height', DEFAULT_HEIGHT_M)), 'poly': poly,
                                 'bbox': (min(lats), max(lats), min(lons), max(lons))})
            # маски считаются без блокировки (начало координат растра после установки не меняется),
            # под блокировкой — только быстрая запись в плитки
            masks = [self._building_mask(rec) for rec in recs]
            with self._lock:
                for rec, m in zip(recs, masks):
                    self.buildings.append(rec)
                    self._stamp(rec, *m)
            added += len(recs)
        return added

    def _to_cells(self, lat, lon):
        lat0, lon0 = self._origin
        x = (np.asarray(lon, dtype=float) - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)) / GRID_RES_M
        y = (np.asarray(lat, dtype=float) - lat0) * M_PER_DEG_LAT / GRID_RES_M
        return x, y

    def _rasterize(self, b):
        self._stamp(b, *self._building_mask(b))

    def _building_mask(self, b):
        """Маска ячеек внутри контура: (mask[h, w], x0, y0). Правило чёт-нечет по центрам ячеек.

        Построчная развёртка: для каждой строки считаются точки пересечения с рёбрами, затем
        гистограмма + накопленная сумма дают число пересечений правее каждой ячейки.
        Память O(h·(E + w)) вместо прежних O(h·E·w) — большие промзоны больше не «съедают» гигабайты.
        """
        xs, ys = self._to_cells([p[0] for p in b['poly']], [p[1] for p in b['poly']])
        x0, x1 = int(math.floor(xs.min())), int(math.ceil(xs.max()))
        y0, y1 = int(math.floor(ys.min())), int(math.ceil(ys.max()))
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        py = y0 + np.arange(h) + 0.5                          # центры ячеек по строкам
        ex1, ey1 = xs, ys
        ex2, ey2 = np.roll(xs, -1), np.roll(ys, -1)
        valid = (ey1[None, :] > py[:, None]) != (ey2[None, :] > py[:, None])        # [h, E]
        dy = np.where(ey2 == ey1, 1.0, ey2 - ey1)
        xc = ex1[None, :] + (py[:, None] - ey1[None, :]) * ((ex2 - ex1) / dy)[None, :]
        rows, cols = np.nonzero(valid)
        # ячейка j (центр x0 + j + 0.5) левее пересечения xc  <=>  j < xc - x0 - 0.5
        k = np.clip(np.ceil(xc[rows, cols] - x0 - 0.5), 0, w).astype(np.intp)
        hist = np.zeros((h, w + 1), dtype=np.int32)
        np.add.at(hist, (rows, k), 1)
        right = np.cumsum(hist[:, ::-1], axis=1)[:, ::-1]     # right[:, j] = пересечений с k >= j
        mask = (right[:, 1:] & 1).astype(bool)                # для ячейки j нужны k > j
        if not mask.any():                                    # совсем крошечное здание
            mask = np.zeros((h, w), dtype=bool)
            mask[min(h - 1, int(ys.mean() - y0)), min(w - 1, int(xs.mean() - x0))] = True
        return mask, x0, y0

    def _stamp(self, b, mask, x0, y0):
        """Записывает маску здания в плитки (вызывать под self._lock)."""
        h, w = mask.shape
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
        win = self.height_window(lat, lon, half_m)
        if win is None:
            return None
        hw, px, py = win
        return hw >= max(1.0, float(min_height)), px, py

    def height_window(self, lat, lon, half_m):
        """Окно растра высот (uint8, м) вокруг точки: (heights[H×W], px, py) или None, если данных нет."""
        with self._lock:
            if self._origin is None or not self._tiles:
                return None
            cache_key = (round(lat, 6), round(lon, 6), float(half_m), id(getattr(self, "terrain_data", None)))
            if self._last_win_key == cache_key and self._last_win is not None:
                win, px, py = self._last_win
                return win.copy(), px, py

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
        terrain_data = getattr(self, "terrain_data", None)
        if terrain_data is not None:
            t_elev = terrain_data.get_elevation(lat, lon)
            if t_elev is not None:
                # Векторизованно вычисляем координаты ячеек окна
                m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
                dx_m = (np.arange(side) - (cx - gx0)) * GRID_RES_M
                dy_m = (np.arange(side) - (cy - gy0)) * GRID_RES_M
                # Выборочно сэмплируем сетку рельефа (шаг ~30 м соответствует нативному SRTM DEM)
                step = 24
                sub_y = np.arange(0, side, step)
                sub_x = np.arange(0, side, step)
                grid_dy, grid_dx = np.meshgrid(dy_m[sub_y], dx_m[sub_x], indexing="ij")
                pts_lat = lat + grid_dy.ravel() / M_PER_DEG_LAT
                pts_lon = lon + grid_dx.ravel() / m_lon
                sub_elevs = terrain_data.elevations_at(pts_lat, pts_lon, allow_fetch=False).reshape(grid_dy.shape)
                rel_elev = sub_elevs - t_elev
                # Интерполируем на полный размер окна
                if rel_elev.size > 0 and np.any(rel_elev > 1.0):
                    full_rel = np.repeat(np.repeat(rel_elev, step, axis=0), step, axis=1)[:side, :side]
                    pos_relief = np.clip(np.maximum(0.0, full_rel), 0, 255).astype(np.uint8)
                    win = np.maximum(win, pos_relief)

        with self._lock:
            self._last_win_key = cache_key
            self._last_win = (win.copy(), cx - gx0, cy - gy0)
        return win, cx - gx0, cy - gy0

    def _gather(self, gx, gy):
        """Высоты по целым глобальным ячейкам (вызывать под self._lock)."""
        out = np.zeros(gx.shape, dtype=float)
        tx, ty = gx // TILE, gy // TILE
        for key in set(zip(ty.tolist(), tx.tolist())):
            tile = self._tiles.get(key)
            if tile is None:
                continue
            m = (ty == key[0]) & (tx == key[1])
            out[m] = tile[gy[m] - key[0] * TILE, gx[m] - key[1] * TILE]
        return out

    def heights_along(self, lat, lon, ux, uy, dists_m):
        """Высоты зданий (м, 0 — пусто) вдоль луча из точки; ux/uy — единичный вектор (восток, север)."""
        dists_m = np.asarray(dists_m, dtype=float)
        with self._lock:
            if self._origin is None or not self._tiles or dists_m.size == 0:
                b_heights = np.zeros(dists_m.shape, dtype=float)
            else:
                cx, cy = self._to_cells(lat, lon)
                gx = np.floor(float(cx) + ux * dists_m / GRID_RES_M).astype(np.int64)
                gy = np.floor(float(cy) + uy * dists_m / GRID_RES_M).astype(np.int64)
                b_heights = self._gather(gx, gy)

        terrain_data = getattr(self, "terrain_data", None)
        if terrain_data is not None and dists_m.size > 0:
            t_rel = terrain_data.heights_along(lat, lon, ux, uy, dists_m)
            # Неровности рельефа (возвышенности) добавляются к профилю высот
            return np.maximum(0.0, b_heights + np.maximum(0.0, t_rel))
        return b_heights

    def heights_at(self, lats, lons):
        """Высоты зданий (м, 0 — пусто) в массиве точек."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        with self._lock:
            if self._origin is None or not self._tiles or lats.size == 0:
                return np.zeros(lats.shape, dtype=float)
            cx, cy = self._to_cells(lats, lons)
            return self._gather(np.floor(cx).astype(np.int64), np.floor(cy).astype(np.int64))

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
