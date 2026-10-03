import math
import numpy as np

NUM_RAYS = 180
GRID_RES_M = 1.2
GRID_SIZE = 1000
G_TX_BASE = 14.0
G_RX = 0.0

THRESHOLDS = [
    ("green",     -55),
    ("excellent", -70),
    ("good",      -85),
    ("weak",      -105),
]

def get_effective_tx_gain(base_g_tx, beamwidth_deg):
    if beamwidth_deg >= 360.0:
        return base_g_tx
    boost = 10.0 * math.log10(360.0 / max(beamwidth_deg, 10.0)) * 0.65
    return base_g_tx + min(boost, 8.0)

def city_path_loss_db(dist_m, freq_mhz, height_m):
    if dist_m <= 1.0: dist_m = 1.0
    wavelength = 300.0 / freq_mhz
    pl_d0 = 20 * math.log10((4 * math.pi * 10.0) / wavelength)
    gamma = 3.4 + (freq_mhz / 1000.0) * 0.35
    if height_m < 10.0:
        gamma += (10.0 - height_m) * 0.15
    return pl_d0 + 10.0 * gamma * math.log10(dist_m / 10.0)

def calc_base_radius(freq_mhz, power_w, height_m, threshold_dbm, beamwidth_deg=360.0):
    eff_g_tx = get_effective_tx_gain(G_TX_BASE, beamwidth_deg)
    p_tx_dbm = 10 * math.log10(power_w * 1000.0) if power_w > 0 else 0.0
    budget = p_tx_dbm + eff_g_tx + G_RX - threshold_dbm
    if budget <= 0: return 0.0
    max_cap = 350.0 if height_m <= 6.0 else (1200.0 if freq_mhz >= 1800 else 3500.0)
    low, high = 1.0, max_cap
    for _ in range(12):
        mid = (low + high) / 2.0
        if city_path_loss_db(mid, freq_mhz, height_m) <= budget: low = mid
        else: high = mid
    return low

def calc_all_base_radii(freq_mhz, power_w, height_m=35.0, beamwidth_deg=360.0):
    return {name: calc_base_radius(freq_mhz, power_w, height_m, th, beamwidth_deg=beamwidth_deg) for name, th in THRESHOLDS}

def build_local_occupancy_grid(lat, lon, height_m, building_data):
    grid = np.zeros((GRID_SIZE, GRID_SIZE), dtype=np.float32)
    if not building_data or not hasattr(building_data, 'buildings'):
        return grid

    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    center_idx = GRID_SIZE // 2

    for b in building_data.buildings:
        if b.get('height', 15.0) < height_m:
            continue
        min_lat, max_lat, min_lon, max_lon = b['bbox']
        y0 = int(center_idx + (min_lat - lat) * m_per_deg_lat / GRID_RES_M)
        y1 = int(center_idx + (max_lat - lat) * m_per_deg_lat / GRID_RES_M)
        x0 = int(center_idx + (min_lon - lon) * m_per_deg_lon / GRID_RES_M)
        x1 = int(center_idx + (max_lon - lon) * m_per_deg_lon / GRID_RES_M)

        y0, y1 = max(0, min(GRID_SIZE-1, y0)), max(0, min(GRID_SIZE-1, y1))
        x0, x1 = max(0, min(GRID_SIZE-1, x0)), max(0, min(GRID_SIZE-1, x1))
        if y0 <= y1 and x0 <= x1:
            grid[y0:y1+1, x0:x1+1] = 1.0
    return grid

def is_in_sector(angle_deg, azimuth_deg, beamwidth_deg):
    if beamwidth_deg >= 360.0:
        return True
    diff = (angle_deg - azimuth_deg + 540.0) % 360.0 - 180.0
    return abs(diff) <= (beamwidth_deg / 2.0)

def ray_tracing(lat, lon, freq_mhz, power_w, height_m, wall_loss_db_val,
                building_data=None, azimuth=0.0, beamwidth=360.0):
    base_radii = calc_all_base_radii(freq_mhz, power_w, height_m, beamwidth_deg=beamwidth)
    max_r = base_radii["weak"]
    if max_r <= 0: return []

    grid = build_local_occupancy_grid(lat, lon, height_m, building_data)
    center_idx = GRID_SIZE // 2
    max_steps = int(max_r / GRID_RES_M)

    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))

    ray_results = []
    angles = [i * (360.0 / NUM_RAYS) for i in range(NUM_RAYS)]

    for a in angles:
        if not is_in_sector(a, azimuth, beamwidth):
            continue

        rad = math.radians(a)
        dx = math.sin(rad)
        dy = math.cos(rad)

        hit_dist_m = max_r
        for step in range(1, max_steps):
            dist_m = step * GRID_RES_M
            cx = int(center_idx + (dist_m * dx) / GRID_RES_M)
            cy = int(center_idx + (dist_m * dy) / GRID_RES_M)
            if not (0 <= cx < GRID_SIZE and 0 <= cy < GRID_SIZE):
                break
            if grid[cy, cx] > 0.5:
                hit_dist_m = dist_m
                break

        r_green = min(base_radii["green"], hit_dist_m)
        r_exc = min(base_radii["excellent"], hit_dist_m)
        r_good = min(base_radii["good"], hit_dist_m)
        r_weak = min(base_radii["weak"], hit_dist_m)

        for z_name, r_val in [("green", r_green), ("excellent", r_exc), ("good", r_good), ("weak", r_weak)]:
            dx_m = dx * r_val
            dy_m = dy * r_val
            ray_results.append({
                "angle": a,
                "lat": lat + dy_m / m_per_deg_lat,
                "lon": lon + dx_m / m_per_deg_lon,
                "dist_m": r_val,
                "zone": z_name
            })

    return ray_results

def calc_radii_from_rays(ray_results):
    zones = {"green": 0.0, "excellent": 0.0, "good": 0.0, "weak": 0.0}
    for r in ray_results:
        z = r.get("zone")
        if z in zones:
            zones[z] = max(zones[z], r.get("dist_m", 0.0))
    return (round(zones["green"]/1000.0,3), round(zones["excellent"]/1000.0,3), round(zones["good"]/1000.0,3), round(zones["weak"]/1000.0,3))