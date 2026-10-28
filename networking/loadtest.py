"""Тест нагрузки сети: абоненты подключаются к вышкам, генерируют трафик, он идёт по транспортной сети.

Модель (упрощённая, оценочная — не заменяет сетевой симулятор уровня пакетов):
  1. Абоненты размещаются на участке (часть в зданиях, часть в «горячих точках»).
  2. Уровень сигнала (RSRP) от каждой вышки берётся из рассчитанных зон покрытия с учётом зданий.
  3. SINR = сигнал / (шум + интерференция от соседних вышек той же частоты) − запас; по нему —
     спектральная эффективность (Шеннон × 0.75, не выше 5 бит/с/Гц на слой) и число слоёв MIMO.
  4. Абонент подключается к лучшей вышке; если в соте больше активных, чем max_users, — к следующей.
  5. Радиоресурс соты делится по справедливому «water-filling» (равные доли времени, остаток отдаётся тем, кому надо меньше).
  6. Трафик вышки идёт по транспортной сети до ядра; пропускная способность линков делится по max-min fairness
     (отдельно вниз и вверх: линки полнодуплексные).
  7. Задержка = радио + распространение + очереди M/M/1 на линках; потери = недоставленный трафик.
Пороги зон (−55…−105 дБм) трактуются как RSRP (мощность на ресурсный элемент).
"""
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

import geo
from calculations import M_PER_DEG_LAT
from netcatalog import CABLES
from network import ETHERNET_EFFICIENCY

# --------------------------------------------------------------------------- #
#  Профили трафика
# --------------------------------------------------------------------------- #
PROFILES = {          # id: (название, нисходящий Мбит/с в активном состоянии, отношение вверх/вниз)
    "web": ("Веб и соцсети", 3.0, 0.15),
    "video_hd": ("Видео HD", 8.0, 0.03),
    "video_4k": ("Видео 4K", 25.0, 0.02),
    "voice": ("Звонки и мессенджеры", 0.15, 1.0),
    "iot": ("IoT / датчики", 0.05, 1.5),
    "bulk": ("Загрузка файлов", 60.0, 0.10),
}
SCENARIOS = {
    "city": ("Город, час пик", {"web": .50, "video_hd": .25, "video_4k": .04, "voice": .12, "iot": .04, "bulk": .05}),
    "event": ("Стадион / массовое событие", {"web": .30, "video_hd": .40, "video_4k": .10, "voice": .10, "bulk": .10}),
    "iot": ("Датчики и IoT", {"iot": .80, "voice": .10, "web": .10}),
    "night": ("Ночь", {"web": .25, "voice": .05, "iot": .40, "bulk": .30}),
}

COVERAGE_MIN_DBM = -105.0
NOISE_FIGURE_DB = 9.0
SINR_MARGIN_DB = 10.0
NEIGHBOR_LOAD = 0.5
MAX_SE_LAYER = 5.0
UL_RATE_FACTOR = 0.5
STATE_IDLE, STATE_OK, STATE_DEGRADED, STATE_BLOCKED = 0, 1, 2, 3


@dataclass
class LoadConfig:
    n_users: int = 600
    active_share: float = 0.30
    steps: int = 20
    indoor_share: float = 0.70
    hotspots: bool = True
    hotspot_share: float = 0.40
    scenario: str = "city"
    require_ip: bool = False
    seed: int = 1
    area: list = None                 # полигон [(lat, lon)]; None — вокруг вышек


@dataclass
class StepResult:
    active: int
    ok: int
    degraded: int
    blocked_cov: int
    blocked_cap: int
    blocked_transport: int
    offered_dl: float
    served_dl: float
    offered_ul: float
    served_ul: float
    lat_mean: float
    lat_p95: float
    state: np.ndarray
    serving: np.ndarray
    tower_users: np.ndarray
    tower_offered: np.ndarray
    tower_served: np.ndarray
    tower_radio_util: np.ndarray
    tower_bh_util: np.ndarray
    link_util: np.ndarray


@dataclass
class LoadResult:
    cfg: LoadConfig
    lat: np.ndarray
    lon: np.ndarray
    indoor: np.ndarray
    tower_names: list
    link_ids: list
    link_labels: list
    link_caps: np.ndarray
    steps: list
    transport_mode: str               # "network" | "radio_only"
    summary: dict = field(default_factory=dict)
    towers_table: list = field(default_factory=list)
    links_table: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    tower_pos: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Радиочасть
# --------------------------------------------------------------------------- #
def radio_config(tower, net=None):
    """Параметры радиоинтерфейса вышки: из сетевого устройства, иначе по частоте."""
    if net is not None:
        dev = net.devices.get(f"T{tower.db_id}")
        if dev is not None and dev.radio:
            return dict(dev.radio)
    f = tower.freq
    bw = 100.0 if f >= 3000 else (20.0 if f >= 1500 else 10.0)
    return {"tech": "NR" if f >= 3000 else "LTE", "bw_mhz": bw, "layers": 2, "max_users": 300}


def rsrp_matrix(towers, lats, lons):
    """Уровень сигнала [пользователей × вышек], дБм — кусочно-линейно по границам зон -55/-70/-85/-105."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    out = np.full((len(lats), len(towers)), -200.0)
    ys = np.array([-40.0, -55.0, -70.0, -85.0, -105.0])
    for t_i, t in enumerate(towers):
        dx = (lons - t.lon) * M_PER_DEG_LAT * math.cos(math.radians(t.lat))
        dy = (lats - t.lat) * M_PER_DEG_LAT
        n = t.reach.shape[0]
        idx = np.floor(np.degrees(np.arctan2(dx, dy)) % 360.0 / (360.0 / n) + 0.5).astype(int) % n
        d = np.hypot(dx, dy)
        reach = t.reach[idx]                                  # [U, 4]
        xs = np.column_stack([np.zeros(len(d)), reach])       # [U, 5]
        res = np.full(len(d), -200.0)
        for k in range(4):
            lo, hi = xs[:, k], xs[:, k + 1]
            m = (d >= lo) & (d < hi)
            frac = (d - lo) / np.where(hi > lo, hi - lo, 1.0)
            res = np.where(m, ys[k] + frac * (ys[k + 1] - ys[k]), res)
        rw = reach[:, 3]
        beyond = (rw > 0) & (d >= rw)
        res = np.where(beyond, ys[4] - 40.0 * np.log10(np.maximum(d, 1.0) / np.maximum(rw, 1.0)), res)
        out[:, t_i] = res
    return out


def sinr_and_rate(rsrp, towers, radios):
    """SINR (дБ) и пиковая скорость (Мбит/с) для каждой пары абонент–вышка."""
    U, T = rsrp.shape
    p = 10.0 ** (rsrp / 10.0)
    noise = 10.0 ** ((-174.0 + 10.0 * math.log10(15e3) + NOISE_FIGURE_DB) / 10.0)   # мВт на ресурсный элемент (15 кГц)
    freq = np.array([t.freq for t in towers])
    co = (np.abs(freq[:, None] - freq[None, :]) < 5.0).astype(float)                # одна несущая -> мешают друг другу
    interf = NEIGHBOR_LOAD * (p @ co - p)
    sinr_db = 10.0 * np.log10(np.maximum(p / (noise + interf), 1e-12)) - SINR_MARGIN_DB
    sinr_db = np.minimum(sinr_db, 30.0)
    bw = np.array([r["bw_mhz"] for r in radios])
    layers = np.array([float(r["layers"]) for r in radios])
    se = np.minimum(MAX_SE_LAYER, 0.75 * np.log2(1.0 + 10.0 ** (sinr_db / 10.0)))
    layers_eff = np.clip(sinr_db / 8.0, 1.0, layers[None, :])
    rate = bw[None, :] * se * layers_eff
    rate = np.where((rsrp >= COVERAGE_MIN_DBM) & (sinr_db >= -3.0), rate, 0.0)
    return sinr_db, rate


def waterfill(demand, rate):
    """Равные доли времени с перераспределением остатка. Возвращает (обслужено Мбит/с, доля занятого ресурса)."""
    need = demand / rate
    if need.sum() <= 1.0:
        return demand.copy(), float(need.sum())
    order = np.argsort(need)
    x = np.zeros(len(need))
    remaining, k = 1.0, len(need)
    for pos, i in enumerate(order):
        share = remaining / k
        if need[i] <= share:
            x[i] = need[i]
            remaining -= need[i]
            k -= 1
        else:
            x[order[pos:]] = share
            break
    return x * rate, float(min(1.0, x.sum()))


def maxmin(demands, paths, caps):
    """Max-min fairness (progressive filling): потоки по путям из индексов линков, caps — ёмкости."""
    F, E = len(demands), len(caps)
    alloc = np.zeros(F)
    rem = np.array(caps, dtype=float)
    active = np.zeros(F, dtype=bool)
    for f in range(F):
        if demands[f] <= 1e-12:
            continue
        if not paths[f]:
            alloc[f] = demands[f]
        else:
            active[f] = True
    for _ in range(F + E + 2):
        if not active.any():
            break
        cnt = np.zeros(E)
        for f in np.flatnonzero(active):
            for e in paths[f]:
                cnt[e] += 1
        inc = min(float((demands[active] - alloc[active]).min()),
                  float((rem[cnt > 0] / cnt[cnt > 0]).min()) if (cnt > 0).any() else float("inf"))
        inc = max(inc, 0.0)
        for f in np.flatnonzero(active):
            alloc[f] += inc
            for e in paths[f]:
                rem[e] -= inc
        saturated = rem <= 1e-9 + 1e-9 * np.asarray(caps)
        for f in np.flatnonzero(active):
            if demands[f] - alloc[f] <= 1e-9 or any(saturated[e] for e in paths[f]):
                active[f] = False
    return alloc


# --------------------------------------------------------------------------- #
#  Транспорт
# --------------------------------------------------------------------------- #
def transport_paths(net, towers, require_ip=False):
    """Путь от каждой вышки до ядра: [(список индексов линков, задержка мс, причина)], режим."""
    if net is None:
        return [([], 0.0, "")] * len(towers), "radio_only", [], np.zeros(0)
    link_ids = list(net.links)
    idx = {lid: i for i, lid in enumerate(link_ids)}
    caps = np.array([net.status[lid].speed_mbps * ETHERNET_EFFICIENCY if lid in net.status else 0.0
                     for lid in link_ids])
    cores = [d for d in net.devices.values() if d.role == "core"]
    if not cores:
        return [([], 0.0, "")] * len(towers), "radio_only", link_ids, caps
    adj = {}
    for l in net.links.values():
        st = net.status.get(l.id)
        if st is None or st.state == "down":
            continue
        adj.setdefault(l.a_dev, []).append((l.b_dev, l.id))
        adj.setdefault(l.b_dev, []).append((l.a_dev, l.id))
    out = []
    for t in towers:
        did = f"T{t.db_id}"
        if did not in net.devices:
            out.append((None, 0.0, "вышка не добавлена в сеть"))
            continue
        if not net.dev_state.get(did, {}).get("powered", True):
            out.append((None, 0.0, "нет питания: " + net.dev_state[did]["reason"]))
            continue
        if require_ip:
            best = None
            for c in cores:
                if not c.ips:
                    continue
                import ipaddress
                ip = str(ipaddress.ip_interface(next(iter(c.ips.values()))).ip)
                links, hops = net.trace(did, ip)
                if links is not None and (best is None or len(links) < len(best[0])):
                    best = (links, hops)
            if best is None:
                out.append((None, 0.0, "нет IP-маршрута до ядра"))
            else:
                out.append(([idx[l] for l in best[0]], net.path_latency_ms(*best), ""))
            continue
        prev, q, found = {did: None}, deque([did]), None
        core_ids = {c.id for c in cores}
        while q and found is None:
            cur = q.popleft()
            if cur in core_ids and cur != did:
                found = cur
                break
            for nb, lid in adj.get(cur, []):
                if nb not in prev:
                    prev[nb] = (cur, lid)
                    q.append(nb)
        if found is None:
            out.append((None, 0.0, "нет физического пути до ядра"))
            continue
        links, hops, cur = [], [found], found
        while prev[cur] is not None:
            cur, lid = prev[cur]
            links.append(lid)
            hops.append(cur)
        links.reverse()
        hops.reverse()
        out.append(([idx[l] for l in links], net.path_latency_ms(links, hops), ""))
    return out, "network", link_ids, caps


# --------------------------------------------------------------------------- #
#  Размещение абонентов
# --------------------------------------------------------------------------- #
def place_users(cfg, towers, bd, rng):
    if cfg.area and len(cfg.area) >= 3:
        lat_min, lat_max, lon_min, lon_max = geo.polygon_bounds(cfg.area)
    else:
        lat_min = min(t.lat - max(t.r_weak, 0.05) * 1000 / M_PER_DEG_LAT for t in towers)
        lat_max = max(t.lat + max(t.r_weak, 0.05) * 1000 / M_PER_DEG_LAT for t in towers)
        k = M_PER_DEG_LAT * math.cos(math.radians(towers[0].lat))
        lon_min = min(t.lon - max(t.r_weak, 0.05) * 1000 / k for t in towers)
        lon_max = max(t.lon + max(t.r_weak, 0.05) * 1000 / k for t in towers)
    n = cfg.n_users
    t_centers = []
    for t in towers:
        rad = max(getattr(t, "r_weak", 0.5) * 1000.0 * 0.45, 120.0)
        az_rad = math.radians(getattr(t, "azimuth", 0.0))
        d_lat = (rad * math.cos(az_rad)) / M_PER_DEG_LAT if getattr(t, "beamwidth", 360) < 360 else 0.0
        d_lon = (rad * math.sin(az_rad)) / (M_PER_DEG_LAT * math.cos(math.radians(t.lat))) if getattr(t, "beamwidth", 360) < 360 else 0.0
        t_centers.append([t.lat + d_lat, t.lon + d_lon, rad])
    want_in = int(round(n * cfg.indoor_share)) if bd is not None else 0

    def batch(cand_n):
        per_t = max(2, int((cand_n * 0.75) // max(len(t_centers), 1)))
        las, los = [], []
        for clat, clon, rad in t_centers:
            d_r = rng.exponential(rad * 0.45, per_t)
            d_ang = rng.uniform(0, 2 * math.pi, per_t)
            d_la = (d_r * np.cos(d_ang)) / M_PER_DEG_LAT
            d_lo = (d_r * np.sin(d_ang)) / (M_PER_DEG_LAT * math.cos(math.radians(clat)))
            las.append(clat + d_la)
            los.append(clon + d_lo)
        rem = cand_n - sum(len(x) for x in las)
        if rem > 0:
            las.append(rng.uniform(lat_min, lat_max, rem))
            los.append(rng.uniform(lon_min, lon_max, rem))
        la = np.concatenate(las)
        lo = np.concatenate(los)
        ok = (la >= lat_min) & (la <= lat_max) & (lo >= lon_min) & (lo <= lon_max)
        if cfg.area and len(cfg.area) >= 3:
            ok &= geo.points_in_polygon(la, lo, cfg.area)
        return la[ok], lo[ok]

    c_lat, c_lon = batch(n * 8)
    for _ in range(5):                                   # мало зданий — докидываем кандидатов, чтобы набрать «внутри»
        heights = bd.heights_at(c_lat, c_lon) if (bd is not None and len(c_lat)) else np.zeros(len(c_lat))
        if len(c_lat) >= n and (heights > 0).sum() >= want_in:
            break
        more_lat, more_lon = batch(n * 16)
        c_lat, c_lon = np.concatenate([c_lat, more_lat]), np.concatenate([c_lon, more_lon])
    if len(c_lat) == 0:
        raise ValueError("Участок слишком мал — не удалось разместить абонентов")
    heights = bd.heights_at(c_lat, c_lon) if bd is not None else np.zeros(len(c_lat))
    inside = heights > 0
    inside_idx = np.flatnonzero(inside)
    out_idx = np.flatnonzero(~inside)
    rng.shuffle(inside_idx)
    rng.shuffle(out_idx)
    n_in = min(len(inside_idx), want_in)
    chosen = list(inside_idx[:n_in]) + list(out_idx[:n - n_in])
    if len(chosen) < n:                                   # не хватило «внешних» — добираем любыми
        rest = [i for i in range(len(c_lat)) if i not in set(chosen)]
        chosen += rest[:n - len(chosen)]
    if len(chosen) < n:                                   # участок мал: размножаем с повторами
        chosen += list(rng.choice(chosen, n - len(chosen)))
    chosen = np.array(chosen[:n])
    return c_lat[chosen], c_lon[chosen], inside[chosen]


# --------------------------------------------------------------------------- #
#  Основной расчёт
# --------------------------------------------------------------------------- #
def run_load_test(net, towers, bd, cfg, progress=None):
    """Выполняет cfg.steps шагов моделирования. progress(i, n) — для индикатора."""
    if not towers:
        raise ValueError("Нет вышек: поставьте хотя бы одну")
    rng = np.random.default_rng(cfg.seed)
    T, U = len(towers), cfg.n_users
    radios = [radio_config(t, net) for t in towers]
    lat, lon, indoor = place_users(cfg, towers, bd, rng)

    prof_ids = list(SCENARIOS[cfg.scenario][1])
    probs = np.array([SCENARIOS[cfg.scenario][1][p] for p in prof_ids], dtype=float)
    prof = rng.choice(len(prof_ids), U, p=probs / probs.sum())
    base_dl = np.array([PROFILES[prof_ids[i]][1] for i in prof])
    ul_ratio = np.array([PROFILES[prof_ids[i]][2] for i in prof])

    rsrp = rsrp_matrix(towers, lat, lon)
    sinr_db, rate = sinr_and_rate(rsrp, towers, radios)
    K = min(3, T)                                                    # запасных сот на абонента
    cand = np.argsort(-rate, axis=1)[:, :K]                          # лучшие по достижимой скорости
    cand_ok = np.take_along_axis(rate, cand, axis=1) > 0
    max_users = np.array([r["max_users"] for r in radios])
    radio_base_ms = np.array([6.0 if r["tech"] == "NR" else 12.0 for r in radios])

    paths_info, mode, link_ids, caps = transport_paths(net, towers, cfg.require_ip)
    E = len(link_ids)
    link_labels = []
    if net is not None:
        for lid in link_ids:
            l = net.links[lid]
            link_labels.append(f"{net.devices[l.a_dev].name} ↔ {net.devices[l.b_dev].name}")

    steps = []
    for step in range(cfg.steps):
        active = rng.random(U) < cfg.active_share
        mult = np.exp(rng.normal(-0.5 * 0.35 ** 2, 0.35, U))
        dl = base_dl * mult
        ul = dl * ul_ratio
        serving = np.full(U, -1, dtype=np.int16)
        cell_count = np.zeros(T, dtype=int)
        blocked_cov = blocked_cap = 0
        for u in rng.permutation(np.flatnonzero(active)):
            if not cand_ok[u].any():
                blocked_cov += 1
                continue
            for k in range(K):
                if cand_ok[u, k] and cell_count[cand[u, k]] < max_users[cand[u, k]]:
                    serving[u] = cand[u, k]
                    cell_count[cand[u, k]] += 1
                    break
            else:
                blocked_cap += 1

        dl_radio = np.zeros(U)
        ul_radio = np.zeros(U)
        radio_util = np.zeros(T)
        t_off_dl = np.zeros(T)
        t_off_ul = np.zeros(T)
        t_srv_dl = np.zeros(T)
        t_srv_ul = np.zeros(T)
        for t in range(T):
            idx = np.flatnonzero(serving == t)
            if idx.size == 0:
                continue
            r = rate[idx, t]
            s_dl, u_dl = waterfill(np.maximum(dl[idx], 1e-6), r)
            s_ul, u_ul = waterfill(np.maximum(ul[idx], 1e-6), r * UL_RATE_FACTOR)
            dl_radio[idx], ul_radio[idx] = s_dl, s_ul
            radio_util[t] = max(u_dl, u_ul)
            t_off_dl[t], t_off_ul[t] = dl[idx].sum(), ul[idx].sum()
            t_srv_dl[t], t_srv_ul[t] = s_dl.sum(), s_ul.sum()

        # --- транспорт ---
        q_dl = np.ones(T)
        q_ul = np.ones(T)
        link_load_dl = np.zeros(E)
        link_load_ul = np.zeros(E)
        blocked_transport = 0
        tower_ok = np.ones(T, dtype=bool)
        bh_util = np.zeros(T)
        if mode == "network":
            paths = []
            for t in range(T):
                p = paths_info[t][0]
                tower_ok[t] = p is not None
                paths.append(p or [])
            for demand, q, load in ((t_srv_dl, q_dl, link_load_dl), (t_srv_ul, q_ul, link_load_ul)):
                d = np.where(tower_ok, demand, 0.0)
                alloc = maxmin(d, paths, caps)
                for t in range(T):
                    q[t] = (alloc[t] / d[t]) if d[t] > 1e-12 else 1.0
                    for e in paths[t]:
                        load[e] += alloc[t]
            for t in range(T):
                if tower_ok[t] and paths[t]:
                    bh_util[t] = max(((link_load_dl[e] / caps[e]) if caps[e] > 0 else 1.0) for e in paths[t])
            for t in range(T):
                if not tower_ok[t]:
                    blocked_transport += int((serving == t).sum())
        final_dl = dl_radio * np.where(serving >= 0, q_dl[np.maximum(serving, 0)], 0.0)
        final_ul = ul_radio * np.where(serving >= 0, q_ul[np.maximum(serving, 0)], 0.0)
        if mode == "network":
            dead = (serving >= 0) & ~tower_ok[np.maximum(serving, 0)]
            final_dl[dead], final_ul[dead] = 0.0, 0.0

        # --- задержка ---
        util = np.maximum(link_load_dl, link_load_ul) / np.maximum(caps, 1e-9) if E else np.zeros(0)
        t_lat = np.zeros(T)
        for t in range(T):
            rho_r = min(radio_util[t], 0.98)
            lat_t = radio_base_ms[t] + min(2.0 * rho_r / (1 - rho_r), 40.0)
            if mode == "network" and tower_ok[t]:
                lat_t += paths_info[t][1]
                for e in paths_info[t][0]:
                    rho = min(util[e], 0.99)
                    svc_ms = 12000.0 / max(caps[e], 1.0) / 1000.0
                    lat_t += min(svc_ms * rho / (1 - rho), 60.0)
            t_lat[t] = lat_t

        served_mask = (serving >= 0) & active
        ratio = np.where(dl > 0, final_dl / np.maximum(dl, 1e-12), 0.0)
        state = np.full(U, STATE_IDLE, dtype=np.uint8)
        state[active] = STATE_BLOCKED
        state[served_mask & (ratio >= 0.95)] = STATE_OK
        state[served_mask & (ratio < 0.95) & (ratio > 0.0)] = STATE_DEGRADED
        state[served_mask & (ratio <= 0.0)] = STATE_BLOCKED
        lats = t_lat[serving[served_mask & (ratio > 0)]] if (served_mask & (ratio > 0)).any() else np.array([0.0])
        tower_users = np.bincount(serving[serving >= 0], minlength=T)
        steps.append(StepResult(
            active=int(active.sum()), ok=int((state == STATE_OK).sum()), degraded=int((state == STATE_DEGRADED).sum()),
            blocked_cov=blocked_cov, blocked_cap=blocked_cap, blocked_transport=blocked_transport,
            offered_dl=float(dl[active].sum()), served_dl=float(final_dl.sum()),
            offered_ul=float(ul[active].sum()), served_ul=float(final_ul.sum()),
            lat_mean=float(lats.mean()), lat_p95=float(np.percentile(lats, 95)),
            state=state, serving=serving, tower_users=tower_users, tower_offered=t_off_dl,
            tower_served=np.bincount(np.maximum(serving, 0), weights=final_dl * (serving >= 0), minlength=T),
            tower_radio_util=radio_util, tower_bh_util=bh_util, link_util=util))
        if progress:
            progress(step + 1, cfg.steps)

    res = LoadResult(cfg, lat, lon, indoor, [t.name for t in towers], link_ids, link_labels, caps, steps, mode,
                     tower_pos=[(t.lat, t.lon) for t in towers])
    _summarize(res, towers, net, paths_info)
    return res


def _summarize(res, towers, net, paths_info):
    S = res.steps
    off = np.array([s.offered_dl for s in S])
    srv = np.array([s.served_dl for s in S])
    act = np.array([s.active for s in S], dtype=float)
    blocked = np.array([s.blocked_cov + s.blocked_cap + s.blocked_transport for s in S], dtype=float)
    peak = int(np.argmax(off))
    undelivered = 1.0 - srv.sum() / max(off.sum(), 1e-12)
    res.summary = {
        "peak_step": peak, "users": res.cfg.n_users, "active_mean": float(act.mean()),
        "offered_mean": float(off.mean()), "offered_peak": float(off.max()),
        "served_mean": float(srv.mean()), "served_peak": float(srv.max()),
        "offered_ul_mean": float(np.mean([s.offered_ul for s in S])),
        "served_ul_mean": float(np.mean([s.served_ul for s in S])),
        "undelivered_pct": float(max(undelivered, 0.0) * 100.0),
        "ok_pct": float(np.mean([s.ok / max(s.active, 1) for s in S]) * 100.0),
        "degraded_pct": float(np.mean([s.degraded / max(s.active, 1) for s in S]) * 100.0),
        "blocked_pct": float(blocked.sum() / max(act.sum(), 1.0) * 100.0),
        "blocked_cov": float(np.mean([s.blocked_cov for s in S])),
        "blocked_cap": float(np.mean([s.blocked_cap for s in S])),
        "blocked_transport": float(np.mean([s.blocked_transport for s in S])),
        "lat_mean": float(np.mean([s.lat_mean for s in S])), "lat_p95": float(np.max([s.lat_p95 for s in S])),
        "indoor_pct": float(res.indoor.mean() * 100.0),
    }
    T = len(towers)
    for t in range(T):
        res.towers_table.append({
            "name": towers[t].name, "users_mean": float(np.mean([s.tower_users[t] for s in S])),
            "offered_peak": float(max(s.tower_offered[t] for s in S)),
            "served_peak": float(max(s.tower_served[t] for s in S)),
            "radio_util_peak": float(max(s.tower_radio_util[t] for s in S)),
            "bh_util_peak": float(max(s.tower_bh_util[t] for s in S)),
            "transport_issue": (paths_info[t][2] if res.transport_mode == "network" and paths_info[t][0] is None else "")})
    for e, lid in enumerate(res.link_ids):
        res.links_table.append({"id": lid, "label": res.link_labels[e], "cap": float(res.link_caps[e]),
                                "util_peak": float(max(s.link_util[e] for s in S)),
                                "util_mean": float(np.mean([s.link_util[e] for s in S]))})
    res.recommendations = _recommend(res, towers, net)


def _next_speed_hint(net, lid):
    l = net.links[lid]
    c = CABLES[l.cable]
    st = net.status[lid].speed_mbps
    if c["group"] == "radio":
        return "перейдите на диапазон с большей ёмкостью (E-band) или добавьте второй хоп"
    if c["group"] == "copper":
        return "замените на оптику 10G (SFP+) или Cat6A 10G"
    for nxt in (10000, 25000, 40000, 100000, 400000):
        if nxt > st:
            return f"перейдите на {nxt // 1000}G оптику/DAC"
    return "добавьте параллельный путь (второй линк/кольцо)"


def _recommend(res, towers, net):
    s, rec = res.summary, []
    if s["blocked_cov"] > 0.5 and s["blocked_pct"] > 1:
        rec.append(f"{s['blocked_cov']:.0f} активных абонентов в среднем вне покрытия — расширьте покрытие "
                   "(добавьте вышки в «слепых» местах, посмотрите тепловую карту на карте).")
    if s["blocked_cap"] > 0.5:
        rec.append(f"Соты упираются в лимит подключений ({s['blocked_cap']:.0f} абонентов отклонено) — "
                   "добавьте соту/сектор или увеличьте max_users в настройках БС.")
    for row in res.towers_table:
        if row["radio_util_peak"] > 0.9:
            rec.append(f"{row['name']}: радиоресурс занят на {row['radio_util_peak'] * 100:.0f}% — "
                       "увеличьте полосу/число слоёв MIMO или разгрузите соседней вышкой.")
        if row["transport_issue"]:
            rec.append(f"{row['name']}: нет транспорта до ядра ({row['transport_issue']}).")
    if net is not None:
        for row in sorted(res.links_table, key=lambda r: -r["util_peak"]):
            if row["util_peak"] > 0.8:
                rec.append(f"Линк {row['label']}: загрузка до {row['util_peak'] * 100:.0f}% — "
                           f"{_next_speed_hint(net, row['id'])}.")
    if s["undelivered_pct"] < 1 and not rec:
        rec.append("Сеть справляется с нагрузкой: недоставленного трафика меньше 1%, узких мест не обнаружено.")
    return rec
