import sqlite3
import base64
from calculations import ray_tracing, calc_radii_from_rays

COLOR_MAP = {
    "Красный": [255, 0, 0, 255],
    "Зеленый": [0, 255, 0, 255],
    "Синий": [0, 0, 255, 255],
    "Черный": [30, 30, 30, 255]
}

DEFAULT_PRESETS = {
    "Низкая микросота": {
        "freq": 1800.0,
        "power": 10.0,
        "height": 5.0,
        "tilt": 6.0,
        "wall_loss": 20.0,
        "color": [255, 0, 0, 255],
        "model_path": None,
        "scale": 1.0,
        "azimuth": 0.0,
        "beamwidth": 360.0
    }
}

class Tower:
    def __init__(self, name, lat, lon, freq, power, height, tilt, wall_loss, color, model_path=None, scale=1.0, building_data=None, azimuth=0.0, beamwidth=360.0):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.freq = freq
        self.power = power
        self.height = height
        self.tilt = tilt
        self.wall_loss = wall_loss
        self.color = color
        self.model_path = model_path
        self.scale = scale
        self.azimuth = azimuth
        self.beamwidth = beamwidth

        self.model_b64 = None
        if model_path:
            try:
                with open(model_path, "rb") as f:
                    b64_str = base64.b64encode(f.read()).decode("utf-8")
                    self.model_b64 = f"data:model/gltf-binary;base64,{b64_str}"
            except Exception as e:
                print(f"Error loading model {model_path}: {e}")

        self.ray_results = ray_tracing(
            lat, lon, freq, power, height, wall_loss,
            building_data=building_data, azimuth=azimuth, beamwidth=beamwidth
        )
        r_g, r_exc, r_good, r_w = calc_radii_from_rays(self.ray_results)
        self.r_green = r_g
        self.r_exc = r_exc
        self.r_good = r_good
        self.r_weak = r_w

    def to_dict(self):
        return {
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "freq": self.freq,
            "power": self.power,
            "height": self.height,
            "tilt": self.tilt,
            "wall_loss": self.wall_loss,
            "color": self.color,
            "scale": self.scale,
            "azimuth": self.azimuth,
            "beamwidth": self.beamwidth,
            "model_b64": self.model_b64,
            "r_green": self.r_green,
            "r_exc": self.r_exc,
            "r_good": self.r_good,
            "r_weak": self.r_weak,
            "ray_results": self.ray_results
        }

    @classmethod
    def from_dict(cls, data, building_data=None):
        return cls(
            name=data["name"],
            lat=data["lat"],
            lon=data["lon"],
            freq=data.get("freq", 1800.0),
            power=data.get("power", 10.0),
            height=data.get("height", 5.0),
            tilt=data.get("tilt", 6.0),
            wall_loss=data.get("wall_loss", 20.0),
            color=data.get("color", [255, 0, 0, 255]),
            model_path=None,
            scale=data.get("scale", 1.0),
            building_data=building_data,
            azimuth=data.get("azimuth", 0.0),
            beamwidth=data.get("beamwidth", 360.0)
        )

    @classmethod
    def from_db_row(cls, row, building_data=None):
        name, lat, lon, freq, power, height, tilt, wall_loss, scale, az, bw = row[1:12]
        return cls(
            name=name, lat=lat, lon=lon, freq=freq, power=power, height=height,
            tilt=tilt, wall_loss=wall_loss, color=[255,0,0,255], scale=scale,
            building_data=building_data, azimuth=az, beamwidth=bw
        )

class TowerDB:
    def __init__(self, db_name="towers.db"):
        self.conn = sqlite3.connect(db_name)
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

    def save_tower(self, tower_dict):
        with self.conn:
            self.conn.execute("""
                INSERT INTO towers (name, lat, lon, freq, power, height, tilt, wall_loss, scale, azimuth, beamwidth)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tower_dict["name"], tower_dict["lat"], tower_dict["lon"],
                tower_dict["freq"], tower_dict["power"], tower_dict["height"],
                tower_dict["tilt"], tower_dict["wall_loss"], tower_dict["scale"],
                tower_dict.get("azimuth", 0.0), tower_dict.get("beamwidth", 360.0)
            ))

    def load_all_towers(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM towers")
        return cursor.fetchall()

    def delete_tower(self, name):
        with self.conn:
            self.conn.execute("DELETE FROM towers WHERE name=?", (name,))

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM towers")