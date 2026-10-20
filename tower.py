import base64
import itertools
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

from calculations import (MODEL_VERSION, NUM_RAYS, areas_from_reach, compute_ray_reach,
                          eirp_dbm, ray_angles, radii_from_reach, reach_to_ray_results, visible_rays)

_REV = itertools.count(1)          # версия результата расчёта: карта перерисовывает только изменившиеся вышки

COLOR_MAP = {
    "Красный": [255, 0, 0, 255],
    "Зеленый": [0, 255, 0, 255],
    "Синий": [0, 0, 255, 255],
    "Черный": [30, 30, 30, 255],
    "Оранжевый": [255, 140, 0, 255],
    "Фиолетовый": [150, 80, 220, 255],
    "Бирюзовый": [0, 190, 200, 255],
}

DEFAULT_PRESETS = {
    "Низкая микросота": {
        "freq": 1800.0, "power": 10.0, "height": 5.0, "tilt": 6.0, "wall_loss": 20.0,
        "color": [255, 0, 0, 255], "model_path": None, "scale": 1.0,
        "azimuth": 0.0, "beamwidth": 360.0,
    },
    "Макросота 800 МГц": {
        "freq": 800.0, "power": 40.0, "height": 35.0, "tilt": 4.0, "wall_loss": 15.0,
        "color": [0, 0, 255, 255], "model_path": None, "scale": 1.0,
        "azimuth": 0.0, "beamwidth": 360.0,
    },
    "Секторная 1800 МГц (65°)": {
        "freq": 1800.0, "power": 20.0, "height": 30.0, "tilt": 6.0, "wall_loss": 20.0,
        "color": [255, 140, 0, 255], "model_path": None, "scale": 1.0,
        "azimuth": 0.0, "beamwidth": 65.0,
    },
    "Пикосота 2600 МГц": {
        "freq": 2600.0, "power": 1.0, "height": 3.0, "tilt": 0.0, "wall_loss": 22.0,
        "color": [150, 80, 220, 255], "model_path": None, "scale": 1.0,
        "azimuth": 0.0, "beamwidth": 360.0,
    },
}

PARAM_KEYS = ("freq", "power", "height", "tilt", "wall_loss", "azimuth", "beamwidth")


class Tower:
    def __init__(self, name, lat, lon, freq, power, height, tilt, wall_loss, color,
                 model_path=None, scale=1.0, building_data=None, azimuth=0.0, beamwidth=360.0,
                 type_name=None, db_id=None, reach=None, fresnel=True, pattern=True, calc=None):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.freq = freq
        self.power = power
        self.height = height
        self.tilt = tilt            # в радиомодель пока не входит (нужен только 3D-модели)
        self.wall_loss = wall_loss
        self.color = list(color)
        self.model_path = model_path
        self.scale = scale
        self.azimuth = azimuth
        self.beamwidth = beamwidth
        self.type_name = type_name
        self.db_id = db_id
        self.fresnel = bool(fresnel)    # дифракция в зоне Френеля
        self.pattern = bool(pattern)    # диаграмма с боковыми/задним лепестками

        self.model_b64 = None
        if model_path:
            try:
                with open(model_path, "rb") as f:
                    b64_str = base64.b64encode(f.read()).decode("utf-8")
                    self.model_b64 = f"data:model/gltf-binary;base64,{b64_str}"
            except Exception as e:
                print(f"Error loading model {model_path}: {e}")

        self.calc_ms = 0.0
        self.used_buildings = False
        self.buildings_pending = False   # зоны посчитаны без зданий: район ещё загружается
        self.walls_total = 0
        self._rays_js = None
        self.calc = dict(calc or {})    # чем посчитаны зоны: версия модели, флаги, здания (для пересчёта при старте)
        if reach is not None:                      # готовый результат (из БД / импорта)
            self.reach = np.asarray(reach, dtype=float)
            self.angles = ray_angles(len(self.reach))
            self.used_buildings = bool(self.calc.get("used_buildings", False))
            self.buildings_pending = bool(self.calc.get("pending", False))
            self._update_derived()
        else:
            self.recalculate(building_data)

    # --- расчёт ---------------------------------------------------------
    def recalculate(self, building_data=None):
        t0 = time.perf_counter()
        res = compute_ray_reach(self.lat, self.lon, self.freq, self.power, self.height,
                                self.wall_loss, building_data=building_data,
                                azimuth=self.azimuth, beamwidth=self.beamwidth,
                                fresnel=self.fresnel, pattern=self.pattern)
        self.angles, self.reach = res.angles, res.reach
        self.used_buildings, self.walls_total = res.used_buildings, res.walls_total
        self.calc_ms = (time.perf_counter() - t0) * 1000.0
        self.calc = {"v": MODEL_VERSION, "fresnel": self.fresnel, "pattern": self.pattern,
                     "buildings": building_data is not None}
        self._update_derived()

    def needs_recalc(self, use_buildings, fresnel, pattern):
        """Сохранённые зоны устарели: другая версия модели/настройки или здания тогда не были загружены."""
        c = self.calc
        return (self.buildings_pending or c.get("v") != MODEL_VERSION or c.get("fresnel") != bool(fresnel)
                or c.get("pattern") != bool(pattern) or c.get("buildings") != bool(use_buildings))

    def _update_derived(self):
        self.rev = next(_REV)
        self._rays_js = None
        self.r_green, self.r_exc, self.r_good, self.r_weak = radii_from_reach(self.reach)
        self.areas = areas_from_reach(self.reach)            # км²: green, excellent, good, weak
        self.eirp_dbm = eirp_dbm(self.power, self.beamwidth)

    @property
    def ray_results(self):
        """Формат прежней версии (список точек границ зон)."""
        return reach_to_ray_results(self.lat, self.lon, self.angles, self.reach,
                                    self.azimuth, self.beamwidth)

    # --- сериализация ---------------------------------------------------
    def params(self):
        return {k: getattr(self, k) for k in PARAM_KEYS}

    def to_dict(self):
        """Полное описание вышки (экспорт/импорт, БД)."""
        d = {
            "id": self.db_id, "name": self.name, "type_name": self.type_name,
            "lat": self.lat, "lon": self.lon, **self.params(),
            "color": self.color, "scale": self.scale,
            "r_green": self.r_green, "r_exc": self.r_exc, "r_good": self.r_good, "r_weak": self.r_weak,
            "area_km2": list(self.areas),
            "reach": np.round(self.reach, 1).tolist(),
            "calc": dict(self.calc, used_buildings=self.used_buildings, pending=self.buildings_pending),
        }
        return d

    def to_js(self):
        """Компактный вид для карты: лучи главного сектора и лепестков, где есть сигнал.

        Лучи считаются один раз (векторно) и кэшируются. rev — версия расчёта: карта
        перестраивает полигоны только у вышек, чья версия изменилась.
        """
        if self._rays_js is None:
            sel = visible_rays(self.angles, self.reach, self.azimuth, self.beamwidth)
            self._rays_js = np.column_stack([np.round(self.angles[sel], 2),
                                             np.round(self.reach[sel], 1)]).tolist()
        return {"id": self.db_id, "name": self.name, "lat": self.lat, "lon": self.lon,
                "height": self.height, "tilt": self.tilt, "scale": self.scale, "color": self.color,
                "azimuth": self.azimuth, "beamwidth": self.beamwidth, "rev": self.rev,
                "full": len(self._rays_js) >= NUM_RAYS,            # лучи по кругу (с задним лепестком)
                "model_b64": self.model_b64, "rays": self._rays_js}

    @classmethod
    def from_dict(cls, data, building_data=None, recalc=True):
        """recalc=False — использовать сохранённые зоны (если они есть в data)."""
        reach = None if (recalc or not data.get("reach")) else data["reach"]
        return cls(
            name=data["name"], lat=data["lat"], lon=data["lon"],
            freq=data.get("freq", 1800.0), power=data.get("power", 10.0),
            height=data.get("height", 5.0), tilt=data.get("tilt", 6.0),
            wall_loss=data.get("wall_loss", 20.0), color=data.get("color", [255, 0, 0, 255]),
            model_path=None, scale=data.get("scale", 1.0), building_data=building_data,
            azimuth=data.get("azimuth", 0.0), beamwidth=data.get("beamwidth", 360.0),
            type_name=data.get("type_name"), db_id=data.get("id"), reach=reach,
            calc=data.get("calc") if reach is not None else None,
        )

    @classmethod
    def from_db_row(cls, row, building_data=None):
        """row — sqlite3.Row/dict (новый формат) или кортеж старой версии БД."""
        if isinstance(row, (tuple, list)):
            keys = ("id", "name", "lat", "lon", "freq", "power", "height", "tilt",
                    "wall_loss", "scale", "azimuth", "beamwidth")
            row = dict(zip(keys, row))
        else:
            row = {k: row[k] for k in row.keys()}
        color = json.loads(row["color"]) if row.get("color") else [255, 0, 0, 255]
        reach = json.loads(row["reach"]) if row.get("reach") else None
        try:
            calc = json.loads(row["calc"]) if row.get("calc") else None
        except ValueError:
            calc = None
        return cls(
            name=row["name"], lat=row["lat"], lon=row["lon"], freq=row["freq"], power=row["power"],
            height=row["height"], tilt=row["tilt"], wall_loss=row["wall_loss"], color=color,
            scale=row["scale"], building_data=building_data, azimuth=row["azimuth"],
            beamwidth=row["beamwidth"], type_name=row.get("type_name"), db_id=row["id"], reach=reach,
            calc=calc,
        )


class TowerDB:
    DEFAULT_PATH = str(Path(__file__).with_name("towers.db"))

    def __init__(self, db_name=None):
        self.conn = sqlite3.connect(db_name or self.DEFAULT_PATH)
        self.conn.row_factory = sqlite3.Row
        # WAL + synchronous=NORMAL: запись не ждёт сброса диска на каждое изменение
        # (раньше каждый кабель/перемещение = fsync, на HDD — десятки мс подвисания)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        self.create_table()

    def create_table(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS towers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT, lat REAL, lon REAL, freq REAL, power REAL,
                    height REAL, tilt REAL, wall_loss REAL, scale REAL,
                    azimuth REAL, beamwidth REAL
                )
            """)
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            # миграция БД старых версий: добавляем недостающие столбцы
            have = {r["name"] for r in self.conn.execute("PRAGMA table_info(towers)")}
            for col in ("color", "type_name", "reach", "calc"):
                if col not in have:
                    self.conn.execute(f"ALTER TABLE towers ADD COLUMN {col} TEXT")

    def save_tower(self, tower_dict):
        """Сохраняет вышку, возвращает её id в БД."""
        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO towers (name, lat, lon, freq, power, height, tilt, wall_loss, scale,
                                    azimuth, beamwidth, color, type_name, reach, calc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, self._values(tower_dict))
            return cur.lastrowid

    def update_tower(self, db_id, tower_dict):
        with self.conn:
            self.conn.execute("""
                UPDATE towers SET name=?, lat=?, lon=?, freq=?, power=?, height=?, tilt=?,
                       wall_loss=?, scale=?, azimuth=?, beamwidth=?, color=?, type_name=?, reach=?, calc=?
                WHERE id=?
            """, (*self._values(tower_dict), db_id))

    @staticmethod
    def _values(d):
        return (d["name"], d["lat"], d["lon"], d["freq"], d["power"], d["height"], d["tilt"],
                d["wall_loss"], d.get("scale", 1.0), d.get("azimuth", 0.0), d.get("beamwidth", 360.0),
                json.dumps(d.get("color", [255, 0, 0, 255])), d.get("type_name"),
                json.dumps(d["reach"]) if d.get("reach") is not None else None,
                json.dumps(d["calc"]) if d.get("calc") else None)

    def load_all_towers(self):
        return self.conn.execute("SELECT * FROM towers ORDER BY id").fetchall()

    def tower_ids(self):
        return {r[0] for r in self.conn.execute("SELECT id FROM towers")}

    def delete_tower(self, key):
        """key — id (int) или имя (str, удалит все одноимённые)."""
        with self.conn:
            if isinstance(key, int):
                self.conn.execute("DELETE FROM towers WHERE id=?", (key,))
            else:
                self.conn.execute("DELETE FROM towers WHERE name=?", (key,))

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM towers")

    # --- произвольные настройки проекта (типы вышек, зоны) ----------------
    def set_meta(self, key, obj):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (key, json.dumps(obj, ensure_ascii=False)))

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
