"""Радиомодель и расчёт зон покрытия.

Модель: логарифмическая потеря на трассе (``city_path_loss_db``) + многостеночная
модель затухания в зданиях:

* каждое пересечение границы здания (вход или выход) даёт ``wall_loss`` дБ;
* каждый метр пути внутри здания даёт ``INDOOR_LOSS_DB_PER_M`` дБ;
* учитываются только здания, чья высота не меньше высоты подвеса антенны
  (как и в исходной версии: более низкие здания сигнал «перешагивает»).

Расчёт векторизован (numpy): вместо цикла «луч × шаг × здание» берётся ОДНА
выборка растра зданий вдоль всех лучей сразу, затем кумулятивные суммы потерь.
Зоны находятся как первая точка луча, где суммарные потери превысили бюджет.
"""
import math
from dataclasses import dataclass

import numpy as np

NUM_RAYS = 180
GRID_RES_M = 1.2            # шаг растра зданий и шаг трассировки, м
WINDOW_HALF_M = 600.0       # здания учитываются в радиусе до 600 м от вышки
GRID_SIZE = int(2 * WINDOW_HALF_M / GRID_RES_M)   # 1000 (для совместимости)
G_TX_BASE = 14.0
G_RX = 0.0
RX_HEIGHT_M = 1.5
M_PER_DEG_LAT = 111320.0
INDOOR_LOSS_DB_PER_M = 0.5

THRESHOLDS = [
    ("green",     -55),
    ("excellent", -70),
    ("good",      -85),
    ("weak",      -105),
]
ZONE_NAMES = tuple(n for n, _ in THRESHOLDS)
ZONE_THRESH = np.array([t for _, t in THRESHOLDS], dtype=float)
ZONE_LABELS_RU = {
    "green": "Зелёная", "excellent": "Отличная", "good": "Средняя", "weak": "Слабая",
}


# --------------------------------------------------------------------------- #
#  Базовая радиомодель (без зданий)
# --------------------------------------------------------------------------- #
def get_effective_tx_gain(base_g_tx, beamwidth_deg):
    if beamwidth_deg >= 360.0:
        return base_g_tx
    boost = 10.0 * math.log10(360.0 / max(beamwidth_deg, 10.0)) * 0.65
    return base_g_tx + min(boost, 8.0)


def _loss_params(freq_mhz, height_m):
    wavelength = 300.0 / freq_mhz
    pl_d0 = 20 * math.log10((4 * math.pi * 10.0) / wavelength)
    gamma = 3.4 + (freq_mhz / 1000.0) * 0.35
    if height_m < 10.0:
        gamma += (10.0 - height_m) * 0.15
    return pl_d0, gamma


def city_path_loss_db(dist_m, freq_mhz, height_m):
    """Потери на трассе, дБ. Принимает число или numpy-массив."""
    pl_d0, gamma = _loss_params(freq_mhz, height_m)
    d = np.maximum(dist_m, 1.0)
    out = pl_d0 + 10.0 * gamma * np.log10(d / 10.0)
    return float(out) if np.ndim(out) == 0 else out


def max_range_cap_m(freq_mhz, height_m):
    return 350.0 if height_m <= 6.0 else (1200.0 if freq_mhz >= 1800 else 3500.0)


def distance_for_loss_db(loss_db, freq_mhz, height_m):
    """Расстояние, на котором потери равны loss_db (обратная к city_path_loss_db).

    Закрытая формула вместо бисекции; результат ограничен потолком дальности.
    ``loss_db <= 0`` означает «бюджета нет» -> 0 м. Принимает число или массив.
    """
    pl_d0, gamma = _loss_params(freq_mhz, height_m)
    cap = max_range_cap_m(freq_mhz, height_m)
    loss = np.asarray(loss_db, dtype=float)
    d = 10.0 * 10.0 ** ((loss - pl_d0) / (10.0 * gamma))
    d = np.where(loss <= 0.0, 0.0, np.clip(d, 1.0, cap))
    return float(d) if d.ndim == 0 else d


def eirp_dbm(power_w, beamwidth_deg=360.0):
    p_tx_dbm = 10 * math.log10(power_w * 1000.0) if power_w > 0 else 0.0
    return p_tx_dbm + get_effective_tx_gain(G_TX_BASE, beamwidth_deg) + G_RX


def calc_base_radius(freq_mhz, power_w, height_m, threshold_dbm, beamwidth_deg=360.0):
    """Радиус зоны без учёта зданий, м."""
    budget = eirp_dbm(power_w, beamwidth_deg) - threshold_dbm
    if budget <= 0:
        return 0.0
    return float(distance_for_loss_db(budget, freq_mhz, height_m))


def calc_all_base_radii(freq_mhz, power_w, height_m=35.0, beamwidth_deg=360.0):
    return {name: calc_base_radius(freq_mhz, power_w, height_m, th, beamwidth_deg=beamwidth_deg)
            for name, th in THRESHOLDS}


def quality_of(rssi_dbm):
    """Название зоны для уровня сигнала или 'none'."""
    if rssi_dbm is None:
        return "none"
    for name, th in THRESHOLDS:
        if rssi_dbm >= th:
            return name
    return "none"


def is_in_sector(angle_deg, azimuth_deg, beamwidth_deg):
    """Попадает ли направление в сектор антенны (число или массив углов)."""
    if beamwidth_deg >= 360.0:
        return np.ones(np.shape(angle_deg), dtype=bool) if np.ndim(angle_deg) else True
    diff = (np.asarray(angle_deg, dtype=float) - azimuth_deg + 540.0) % 360.0 - 180.0
    res = np.abs(diff) <= (beamwidth_deg / 2.0)
    return res if res.ndim else bool(res)


def ray_angles(num_rays=NUM_RAYS):
    return np.arange(num_rays) * (360.0 / num_rays)


# --------------------------------------------------------------------------- #
#  Затухание в зданиях
# --------------------------------------------------------------------------- #
def mask_own_building(hit):
    """Убирает ведущую серию занятых ячеек: здание, на котором стоит вышка."""
    hit = np.asarray(hit, dtype=bool)
    return hit & np.logical_or.accumulate(~hit, axis=-1)


def wall_loss_profile(hit, wall_loss_db):
    """Кумулятивные потери от зданий вдоль лучей.

    :param hit: bool-массив [..., шаги] — ячейка трассы внутри здания
    :param wall_loss_db: потери на одну стену, дБ
    :return: (cum_loss_db, walls, inside_m) той же формы, что hit

    Здание, внутри которого стоит сама вышка (ведущая серия занятых ячеек от
    старта луча), игнорируется: антенна считается установленной на его крыше.
    """
    hit = mask_own_building(hit)
    prev = np.zeros_like(hit)
    prev[..., 1:] = hit[..., :-1]
    walls = np.cumsum(hit != prev, axis=-1)
    inside_m = np.cumsum(hit, axis=-1) * GRID_RES_M
    cum = walls * float(wall_loss_db) + inside_m * INDOOR_LOSS_DB_PER_M
    return cum, walls, inside_m


@dataclass
class RayResult:
    angles: np.ndarray          # [R] градусы
    reach: np.ndarray           # [R, 4] метры до границ зон (0 вне сектора)
    used_buildings: bool        # были ли в окне данные о зданиях
    walls_total: int = 0        # сумма пересечённых стен по лучам (для отчёта)


def compute_ray_reach(lat, lon, freq_mhz, power_w, height_m, wall_loss_db,
                      building_data=None, azimuth=0.0, beamwidth=360.0):
    """Дальность до границ четырёх зон по каждому из NUM_RAYS лучей."""
    angles = ray_angles()
    reach = np.zeros((len(angles), len(ZONE_NAMES)))
    in_sec = np.asarray(is_in_sector(angles, azimuth, beamwidth), dtype=bool)

    allowed = eirp_dbm(power_w, beamwidth) - ZONE_THRESH          # [4] допустимые потери
    base = np.asarray(distance_for_loss_db(allowed, freq_mhz, height_m))
    r_max = float(base[-1])
    if r_max <= 0.0 or not in_sec.any():
        return RayResult(angles, reach, False)

    sel = np.flatnonzero(in_sec)
    reach[sel] = base                                              # значение «без зданий»

    half_m = min(r_max, WINDOW_HALF_M)
    n_steps = int(half_m // GRID_RES_M)
    win = None
    if building_data is not None and n_steps >= 1:
        win = building_data.occupancy_window(lat, lon, half_m, height_m)
    if win is None:
        return RayResult(angles, reach, False)

    occ, px, py = win
    a = np.radians(angles[sel])
    s = np.arange(1, n_steps + 1, dtype=float)[None, :]            # шаги в ячейках
    cx = np.floor(px + np.sin(a)[:, None] * s).astype(np.intp)
    cy = np.floor(py + np.cos(a)[:, None] * s).astype(np.intp)
    np.clip(cx, 0, occ.shape[1] - 1, out=cx)
    np.clip(cy, 0, occ.shape[0] - 1, out=cy)

    cum, walls, _ = wall_loss_profile(occ[cy, cx], wall_loss_db)
    total = city_path_loss_db(s[0] * GRID_RES_M, freq_mhz, height_m)[None, :] + cum

    window_end = n_steps * GRID_RES_M
    for z in range(len(ZONE_NAMES)):
        exceed = total > allowed[z]
        failed = exceed[:, -1]                  # потери монотонны: упало в конце == упало где-то
        r = np.empty(len(sel))
        fr = np.flatnonzero(failed)
        if fr.size:                              # граница внутри шага: уточняем по остатку бюджета
            k = exceed[fr].argmax(axis=1)        # индекс первого шага, где бюджет превышен
            cum_prev = np.where(k > 0, cum[fr, np.maximum(k - 1, 0)], 0.0)
            far = distance_for_loss_db(allowed[z] - cum_prev, freq_mhz, height_m)
            r[fr] = np.clip(far, k * GRID_RES_M, (k + 1) * GRID_RES_M)
        if (~failed).any():                      # дошли до края окна — дальше зданий нет
            rem = allowed[z] - cum[~failed, -1]
            r[~failed] = np.maximum(distance_for_loss_db(rem, freq_mhz, height_m), window_end)
        reach[sel, z] = np.minimum(r, base[z])

    return RayResult(angles, reach, True, int(walls[:, -1].sum()))


def reach_to_ray_results(lat, lon, angles, reach, azimuth=0.0, beamwidth=360.0):
    """Формат прежней версии: список dict по лучам и зонам."""
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    out = []
    for i, a in enumerate(angles):
        if not is_in_sector(a, azimuth, beamwidth):
            continue
        rad = math.radians(a)
        dx, dy = math.sin(rad), math.cos(rad)
        for z, name in enumerate(ZONE_NAMES):
            r = float(reach[i, z])
            out.append({"angle": float(a), "lat": lat + dy * r / M_PER_DEG_LAT,
                        "lon": lon + dx * r / m_lon, "dist_m": r, "zone": name})
    return out


def ray_tracing(lat, lon, freq_mhz, power_w, height_m, wall_loss_db_val,
                building_data=None, azimuth=0.0, beamwidth=360.0):
    """Совместимая со старым кодом обёртка: список точек границ зон по лучам."""
    res = compute_ray_reach(lat, lon, freq_mhz, power_w, height_m, wall_loss_db_val,
                            building_data, azimuth, beamwidth)
    return reach_to_ray_results(lat, lon, res.angles, res.reach, azimuth, beamwidth)


def radii_from_reach(reach):
    """Максимальные радиусы зон, км: (green, excellent, good, weak)."""
    r = np.asarray(reach).max(axis=0) / 1000.0
    return tuple(round(float(v), 3) for v in r)


def calc_radii_from_rays(ray_results):
    zones = {"green": 0.0, "excellent": 0.0, "good": 0.0, "weak": 0.0}
    for r in ray_results:
        z = r.get("zone")
        if z in zones:
            zones[z] = max(zones[z], r.get("dist_m", 0.0))
    return (round(zones["green"] / 1000.0, 3), round(zones["excellent"] / 1000.0, 3),
            round(zones["good"] / 1000.0, 3), round(zones["weak"] / 1000.0, 3))


def areas_from_reach(reach):
    """Площади зон, км² (сумма секторов по лучам)."""
    reach = np.asarray(reach)
    dtheta = 2.0 * math.pi / reach.shape[0]
    return tuple(round(float(0.5 * dtheta * np.sum(reach[:, z] ** 2) / 1e6), 4)
                 for z in range(reach.shape[1]))


# --------------------------------------------------------------------------- #
#  Анализ радиолинии «вышка -> точка» (зонд сигнала, профиль трассы)
# --------------------------------------------------------------------------- #
def link_analysis(tower, lat, lon, building_data=None, rx_height_m=RX_HEIGHT_M):
    """Уровень сигнала вышки в точке и профиль трассы.

    ``tower`` — любой объект с полями lat, lon, freq, power, height, wall_loss,
    azimuth, beamwidth. Использует ту же модель и ту же выборку растра, что и
    compute_ray_reach, поэтому границы зон и значения зонда согласованы.
    """
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(tower.lat))
    dxm = (lon - tower.lon) * m_lon
    dym = (lat - tower.lat) * M_PER_DEG_LAT
    dist = math.hypot(dxm, dym)
    az = math.degrees(math.atan2(dxm, dym)) % 360.0
    ux, uy = (dxm / dist, dym / dist) if dist > 0 else (0.0, 1.0)

    n = int(dist // GRID_RES_M)
    d_arr = np.arange(1, n + 1, dtype=float) * GRID_RES_M
    heights = np.zeros(n)
    if building_data is not None and n > 0:
        heights = building_data.heights_along(tower.lat, tower.lon, ux, uy, d_arr)
    hit = mask_own_building(heights >= max(float(tower.height), 1.0))
    cum, walls, inside_m = wall_loss_profile(hit, tower.wall_loss)
    wall_total = float(cum[-1]) if n else 0.0

    pl = city_path_loss_db(dist, tower.freq, tower.height)
    eirp = eirp_dbm(tower.power, tower.beamwidth)
    in_sector = bool(is_in_sector(az, tower.azimuth, tower.beamwidth))
    total = pl + wall_total
    rssi = eirp - total if in_sector else None

    # геометрическая прямая видимость и первая зона Френеля
    wavelength = 300.0 / tower.freq
    los_h = tower.height + (rx_height_m - tower.height) * (d_arr / dist) if n else np.zeros(0)
    d2 = np.maximum(dist - d_arr, 1e-6)
    fresnel_r = np.sqrt(wavelength * d_arr * d2 / dist) if n else np.zeros(0)
    los_clear = bool(not np.any(heights > los_h)) if n else True
    fresnel_clear = float(np.min((los_h - heights) / np.maximum(fresnel_r, 1e-6))) if n else 9.99

    return {
        "distance_m": dist, "azimuth": az, "in_sector": in_sector,
        "eirp_dbm": eirp, "path_loss_db": pl, "wall_loss_db": wall_total,
        "walls": int(walls[-1]) if n else 0,
        "inside_m": float(inside_m[-1]) if n else 0.0,
        "total_loss_db": total, "rssi_dbm": rssi, "quality": quality_of(rssi),
        "los_clear": los_clear, "fresnel_clearance": fresnel_clear,
        "profile": {"dist": d_arr, "heights": heights, "hit": hit,
                    "los_h": los_h, "fresnel_r": fresnel_r,
                    "tx_h": float(tower.height), "rx_h": float(rx_height_m)},
    }
