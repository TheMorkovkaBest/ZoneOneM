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
from functools import lru_cache

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

# Диаграмма направленности секторной антенны (3GPP TR 36.814): A(θ) = −min(12·(θ/θ3дБ)², Am).
# Am — подавление боковых и заднего лепестков: вне главного луча сигнал ослаблен на 25 дБ, а не обнулён.
FRONT_TO_BACK_DB = 25.0
# Дифракция на зданиях ниже антенны, попавших в 1-ю зону Френеля (ITU-R P.526, доминирующее препятствие).
FRESNEL_BLOCK_CELLS = 5          # препятствия и точки приёма берутся блоками по 5 ячеек (6 м)
DIFFRACTION_MAX_DB = 25.0
MODEL_VERSION = 2                # меняется при изменении модели: сохранённые зоны пересчитываются

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


def pattern_attenuation_db(angle_deg, azimuth_deg, beamwidth_deg, pattern=True):
    """Ослабление диаграммы направленности относительно главного направления, дБ (число или массив).

    pattern=False — прежняя «идеальная» модель: полный уровень внутри сектора, вне его сигнала нет (inf).
    """
    angle = np.asarray(angle_deg, dtype=float)
    if beamwidth_deg >= 360.0:
        out = np.zeros(angle.shape)
    else:
        off = np.abs((angle - azimuth_deg + 540.0) % 360.0 - 180.0)
        if pattern:
            out = np.minimum(12.0 * (off / max(beamwidth_deg, 1.0)) ** 2, FRONT_TO_BACK_DB)
        else:
            out = np.where(off <= beamwidth_deg / 2.0, 0.0, np.inf)
    return float(out) if out.ndim == 0 else out


def knife_edge_db(v):
    """Потери дифракции на клиновидном препятствии J(v), дБ (ITU-R P.526, J(v) при v > −0.78)."""
    v = np.asarray(v, dtype=float)
    arg = np.sqrt((v - 0.1) ** 2 + 1.0) + v - 0.1
    out = np.where(v > -0.78, 6.9 + 20.0 * np.log10(np.maximum(arg, 1e-9)), 0.0)
    return np.clip(out, 0.0, DIFFRACTION_MAX_DB)


def fresnel_diffraction_profile(heights, h_tx, freq_mhz, rx_h=RX_HEIGHT_M):
    """Потери дифракции вдоль луча(ей) для приёмника в концах блоков по FRESNEL_BLOCK_CELLS шагов.

    :param heights: [..., шаги] высоты зданий вдоль луча (шаг GRID_RES_M), 0 — пусто;
                    длина дополняется нулями до целого числа блоков
    :return: (loss[..., nb], d_rx[nb]) — потери для приёмника (высота rx_h) на расстоянии d_rx[i].
    Препятствия — здания выше приёмника, но ниже антенны (более высокие считаются стенами,
    см. wall_loss_profile) в блоках j < i, кроме здания, на котором стоит вышка. Берётся
    доминирующее препятствие: max J(v), v = √2·h / r1, h — высота над линией визирования,
    r1 — радиус 1-й зоны Френеля в точке препятствия.
    """
    heights = np.asarray(heights)
    B = FRESNEL_BLOCK_CELLS
    n = heights.shape[-1]
    nb = -(-n // B)
    lead = heights.shape[:-1]
    if nb == 0:
        return np.zeros(lead + (0,)), np.zeros(0)
    if nb * B > n:
        heights = np.concatenate([heights, np.zeros(lead + (nb * B - n,), dtype=heights.dtype)], axis=-1)
    low = np.where((heights > rx_h) & (heights < h_tx) & mask_own_building(heights > 0), heights, 0)
    hb = low.reshape(lead + (nb, B)).max(axis=-1).astype(np.float32)     # [..., nb]
    k, lc, d_rx = _fresnel_geometry(nb, float(h_tx), float(freq_mhz), float(rx_h))
    cols = np.flatnonzero((hb > 0).reshape(-1, nb).any(axis=0))          # блоки, где вообще есть препятствия
    if cols.size == 0:
        return np.zeros(lead + (nb,)), d_rx
    hbc = hb[..., cols]
    hbc = np.where(hbc > 0, hbc, np.float32(-1e4))                       # пустой блок — не препятствие
    v = hbc[..., None, :] * k[:, cols] - lc[:, cols]                      # [..., I, J'] = √2·(h − LOS)/r1
    return knife_edge_db(v.max(axis=-1)), d_rx


@lru_cache(maxsize=64)
def _fresnel_geometry(nb, h_tx, freq_mhz, rx_h):
    """Геометрия трассы, не зависящая от зданий: k = √2/r1 и k·h_LOS для пар (приёмник i, препятствие j)."""
    step = FRESNEL_BLOCK_CELLS * GRID_RES_M
    d_obs = (np.arange(nb) + 0.5) * step                                  # середина блока препятствия
    d_rx = (np.arange(nb) + 1.0) * step                                   # приёмник в конце блока
    D, d1 = d_rx[:, None], d_obs[None, :]                                 # [I, 1], [1, J]
    before = d1 < D - step                                                # только блоки j < i
    r1 = np.sqrt((300.0 / freq_mhz) * d1 * np.maximum(D - d1, 1e-3) / D)  # радиус 1-й зоны Френеля
    los = h_tx + (rx_h - h_tx) * d1 / D                                   # высота линии визирования
    k = np.where(before, np.sqrt(2.0) / r1, 0.0)
    lc = np.where(before, k * los, 1e6)                                   # j >= i: v = −1e6, не учитывается
    k, lc = k.astype(np.float32), lc.astype(np.float32)
    k.setflags(write=False)
    lc.setflags(write=False)
    return k, lc, d_rx


def diffraction_at(profile, d_rx, dist_m):
    """Потери дифракции на расстояниях dist_m (линейно между приёмниками; до первого блока — 0)."""
    dist_m = np.asarray(dist_m, dtype=float)
    if profile.shape[-1] == 0:
        return np.zeros(profile.shape[:-1] + dist_m.shape)
    xp = np.concatenate([[0.0], d_rx])
    fp = np.concatenate([np.zeros(profile.shape[:-1] + (1,)), profile], axis=-1)
    if fp.ndim == 1:
        return np.interp(dist_m, xp, fp)
    # векторная интерполяция для всех лучей сразу (одни и те же узлы xp)
    idx = np.clip(np.searchsorted(xp, dist_m, side="right") - 1, 0, len(xp) - 2)
    t = np.clip((dist_m - xp[idx]) / (xp[idx + 1] - xp[idx]), 0.0, 1.0)
    return fp[..., idx] * (1.0 - t) + fp[..., idx + 1] * t


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
                      building_data=None, azimuth=0.0, beamwidth=360.0, fresnel=True, pattern=True):
    """Дальность до границ четырёх зон по каждому из NUM_RAYS лучей.

    pattern — диаграмма направленности с боковыми/задним лепестками (иначе вне сектора сигнала нет);
    fresnel — дифракция на зданиях ниже антенны в 1-й зоне Френеля.
    Граница зоны на луче — дальняя точка, где потери ещё в пределах бюджета (за ней — только хуже);
    без дифракции потери вдоль луча монотонны, и это совпадает с «первой точкой превышения».
    """
    angles = ray_angles()
    reach = np.zeros((len(angles), len(ZONE_NAMES)))
    att = pattern_attenuation_db(angles, azimuth, beamwidth, pattern)          # [R] ослабление антенны
    allowed = eirp_dbm(power_w, beamwidth) - ZONE_THRESH[None, :] - att[:, None]   # [R, 4]
    active = np.isfinite(att) & (allowed[:, -1] > 0)
    if not active.any():
        return RayResult(angles, reach, False)

    sel = np.flatnonzero(active)
    allowed = allowed[sel]
    base = np.asarray(distance_for_loss_db(allowed, freq_mhz, height_m))       # [S, 4] «без зданий»
    reach[sel] = base

    half_m = min(float(base[:, -1].max()), WINDOW_HALF_M)
    n_steps = int(half_m // GRID_RES_M)
    win = None
    if building_data is not None and n_steps >= 1:
        win = building_data.height_window(lat, lon, half_m)
    if win is None:
        return RayResult(angles, reach, False)

    hw, px, py = win
    a = np.radians(angles[sel])
    s = np.arange(1, n_steps + 1, dtype=float)[None, :]            # шаги в ячейках
    cx = np.floor(px + np.sin(a)[:, None] * s).astype(np.intp)
    cy = np.floor(py + np.cos(a)[:, None] * s).astype(np.intp)
    np.clip(cx, 0, hw.shape[1] - 1, out=cx)
    np.clip(cy, 0, hw.shape[0] - 1, out=cy)
    heights = hw[cy, cx]                                           # [S, шаги] uint8

    dist = s[0] * GRID_RES_M
    extra, walls, _ = wall_loss_profile(heights >= max(1.0, float(height_m)), wall_loss_db)
    if fresnel:
        prof, d_rx = fresnel_diffraction_profile(heights, height_m, freq_mhz)
        extra = extra + diffraction_at(prof, d_rx, dist)
    total = city_path_loss_db(dist, freq_mhz, height_m)[None, :] + extra

    window_end = n_steps * GRID_RES_M
    for z in range(len(ZONE_NAMES)):
        ok = total <= allowed[:, z, None]
        tail = ok[:, -1]                         # на краю окна сигнал есть — дальше зданий нет
        r = np.empty(len(sel))
        fr = np.flatnonzero(~tail)
        if fr.size:                              # граница внутри шага: уточняем по остатку бюджета
            last_ok = n_steps - 1 - ok[fr, ::-1].argmax(axis=1)
            last_ok = np.where(ok[fr].any(axis=1), last_ok, -1)
            k = last_ok + 1                      # первый шаг, после которого бюджет превышен до конца окна
            prev = np.where(k > 0, extra[fr, np.maximum(k - 1, 0)], 0.0)
            far = distance_for_loss_db(allowed[fr, z] - prev, freq_mhz, height_m)
            r[fr] = np.clip(far, k * GRID_RES_M, (k + 1) * GRID_RES_M)
        if tail.any():
            rem = allowed[tail, z] - extra[tail, -1]
            r[tail] = np.maximum(distance_for_loss_db(rem, freq_mhz, height_m), window_end)
        reach[sel, z] = np.minimum(r, base[:, z])

    return RayResult(angles, reach, True, int(walls[:, -1].sum()))


def angle_offset(angle_deg, azimuth_deg):
    """Отклонение направления от азимута, −180…180°."""
    return (angle_deg - azimuth_deg + 540.0) % 360.0 - 180.0


def visible_rays(angles, reach, azimuth, beamwidth):
    """Лучи для отрисовки/экспорта: главный сектор и всё, где сигнал есть (боковые и задний лепестки)."""
    return np.asarray(is_in_sector(angles, azimuth, beamwidth), dtype=bool) | (np.asarray(reach)[:, -1] > 0)


def reach_to_ray_results(lat, lon, angles, reach, azimuth=0.0, beamwidth=360.0):
    """Формат прежней версии: список dict по лучам и зонам."""
    m_lon = M_PER_DEG_LAT * math.cos(math.radians(lat))
    out = []
    show = visible_rays(angles, reach, azimuth, beamwidth)
    for i, a in enumerate(angles):
        if not show[i]:
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

    # дифракция — тем же способом, что в compute_ray_reach (блоки + интерполяция между приёмниками);
    # один лишний пустой блок, чтобы приёмник блока, в котором лежит точка, тоже существовал
    diffraction = 0.0
    if getattr(tower, "fresnel", True) and n:
        B = FRESNEL_BLOCK_CELLS
        padded = np.concatenate([heights, np.zeros((n // B + 1) * B - n)])
        prof, d_rx = fresnel_diffraction_profile(padded, float(tower.height), tower.freq, rx_height_m)
        diffraction = float(diffraction_at(prof, d_rx, n * GRID_RES_M))   # как стены: по последнему шагу

    pl = city_path_loss_db(dist, tower.freq, tower.height)
    eirp = eirp_dbm(tower.power, tower.beamwidth)
    in_sector = bool(is_in_sector(az, tower.azimuth, tower.beamwidth))
    pattern_db = pattern_attenuation_db(az, tower.azimuth, tower.beamwidth, getattr(tower, "pattern", True))
    total = pl + wall_total + diffraction
    rssi = eirp - total - pattern_db if math.isfinite(pattern_db) else None

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
        "diffraction_db": diffraction, "pattern_db": pattern_db if math.isfinite(pattern_db) else None,
        "walls": int(walls[-1]) if n else 0,
        "inside_m": float(inside_m[-1]) if n else 0.0,
        "total_loss_db": total, "rssi_dbm": rssi, "quality": quality_of(rssi),
        "los_clear": los_clear, "fresnel_clearance": fresnel_clear,
        "profile": {"dist": d_arr, "heights": heights, "hit": hit,
                    "los_h": los_h, "fresnel_r": fresnel_r,
                    "tx_h": float(tower.height), "rx_h": float(rx_height_m)},
    }
