"""Алгоритм 1: Оптимизация радиопокрытия (Discrete Azimuth Greedy Set-Cover).

Реализует пошаговый алгоритм автоматической расстановки секторных вышек:
1. Дискретизация целевой зоны на сетку точек (Target Grid).
2. Выявление площадок-кандидатов на крышах доминантных высот (Candidate Sites).
3. Генерация направленных секторов с дискретным шагом азимутов (0°, 45°, 90°, ...).
4. Итеративный жадный отбор (Greedy Selection) по максимальному маржинальному приросту.
5. Финальная очистка избыточных дублей (Redundancy Pruning).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

import c_coverage
import calculations
import coverage
import geo
from tower import Tower


@dataclass
class CandidateSite:
    """Точка-площадка для установки антенны (крыша здания или опора на рельефе)."""
    lat: float
    lon: float
    height: float
    building_id: Optional[str] = None
    name: str = "Площадка"


@dataclass
class CandidateSector:
    """Направленный сектор-кандидат (площадка + конкретный азимут)."""
    site: CandidateSite
    azimuth: float
    beamwidth: float
    covered_indices: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    mask_u64: Optional[np.ndarray] = None
    tower_instance: Optional[Tower] = None

    @property
    def key(self) -> Tuple[float, float, float]:
        return (round(self.site.lat, 6), round(self.site.lon, 6), round(self.azimuth, 1))


def _building_roof_point(poly: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """Находит точку, гарантированно лежащую на кровле здания (внутри полигона)."""
    if not poly:
        return 0.0, 0.0
    c_lat = sum(p[0] for p in poly) / len(poly)
    c_lon = sum(p[1] for p in poly) / len(poly)
    if geo.point_in_polygon(c_lat, c_lon, poly):
        return c_lat, c_lon

    # Если центроид снаружи (например, внутренний двор) — ищем точку внутри
    n = len(poly)
    for i in range(n):
        p1 = poly[i]
        p2 = poly[(i + 1) % n]
        mid_lat = (p1[0] + p2[0]) / 2.0
        mid_lon = (p1[1] + p2[1]) / 2.0
        for factor in (0.05, 0.1, 0.2, 0.3, 0.5):
            test_lat = mid_lat + (c_lat - mid_lat) * factor
            test_lon = mid_lon + (c_lon - mid_lon) * factor
            if geo.point_in_polygon(test_lat, test_lon, poly):
                return test_lat, test_lon

    return poly[0][0], poly[0][1]


def find_candidate_sites(
    area_poly: Sequence[Tuple[float, float]],
    forbidden_polys: Sequence[Sequence[Tuple[float, float]]],
    building_data: Any,
    default_height: float = 30.0,
    cluster_size_m: float = 250.0,
) -> List[CandidateSite]:
    """Находит доминантные высоты (крыши высоких зданий или локальные пики) в каждом кластере."""
    lat_min, lat_max, lon_min, lon_max = geo.polygon_bounds(area_poly)
    lat_mid = (lat_min + lat_max) / 2.0

    dlat = cluster_size_m / calculations.M_PER_DEG_LAT
    dlon = cluster_size_m / (calculations.M_PER_DEG_LAT * math.cos(math.radians(lat_mid)))

    sites: List[CandidateSite] = []
    used_building_ids = set()

    # Извлекаем список зданий, если доступен
    buildings_list = getattr(building_data, "buildings", []) if building_data else []

    lat_steps = np.arange(lat_min + dlat / 2.0, lat_max, dlat)
    lon_steps = np.arange(lon_min + dlon / 2.0, lon_max, dlon)

    # Если область меньше шага кластеризации, обеспечиваем хотя бы один центр
    if len(lat_steps) == 0:
        lat_steps = np.array([lat_mid])
    if len(lon_steps) == 0:
        lon_steps = np.array([(lon_min + lon_max) / 2.0])

    for c_lat in lat_steps:
        for c_lon in lon_steps:
            # Проверяем, попадает ли центр кластера или его окрестность в разрешённую зону
            if not geo.point_in_polygon(c_lat, c_lon, area_poly):
                # Пробуем найти точку кластера, которая внутри полигона
                c_lat_in, c_lon_in = _find_interior_point(c_lat, c_lon, dlat / 2, dlon / 2, area_poly, forbidden_polys)
                if c_lat_in is None:
                    continue
                c_lat, c_lon = c_lat_in, c_lon_in

            if any(geo.point_in_polygon(c_lat, c_lon, fz) for fz in forbidden_polys or []):
                continue

            # Ищем самое высокое здание в радиусе этого кластера
            best_bld = None
            max_bld_h = 0.0

            half_diag = math.hypot(dlat, dlon) * calculations.M_PER_DEG_LAT / 2.0
            for b in buildings_list:
                bid = b.get("id")
                if bid in used_building_ids:
                    continue
                poly = b.get("poly", [])
                if not poly:
                    continue
                # Гарантированная точка на крыше здания
                b_lat, b_lon = _building_roof_point(poly)

                dist_m = math.hypot(
                    (b_lat - c_lat) * calculations.M_PER_DEG_LAT,
                    (b_lon - c_lon) * calculations.M_PER_DEG_LAT * math.cos(math.radians(lat_mid)),
                )
                if dist_m <= half_diag * 1.2:
                    h = float(b.get("height", 0.0))
                    # Здание должно быть внутри полигона задачи и вне запретных зон
                    if geo.point_in_polygon(b_lat, b_lon, area_poly) and not any(
                        geo.point_in_polygon(b_lat, b_lon, fz) for fz in forbidden_polys or []
                    ):
                        if h > max_bld_h:
                            max_bld_h = h
                            best_bld = (bid, b_lat, b_lon, h)

            if best_bld is not None:
                bid, b_lat, b_lon, h = best_bld
                used_building_ids.add(bid)
                sites.append(CandidateSite(
                    lat=b_lat,
                    lon=b_lon,
                    height=h + 5.0,  # Монтаж на кровле: высота здания + 5м мачта
                    building_id=str(bid),
                    name=f"Крыша #{bid} ({h:.0f}м)",
                ))
            else:
                # Если зданий нет — опора/мачта на открытой площадке
                sites.append(CandidateSite(
                    lat=c_lat,
                    lon=c_lon,
                    height=default_height,
                    building_id=None,
                    name=f"Опора {default_height:.0f}м",
                ))

    return sites


def _find_interior_point(
    lat: float, lon: float, dlat: float, dlon: float,
    poly: Sequence[Tuple[float, float]],
    forbidden: Sequence[Sequence[Tuple[float, float]]],
) -> Tuple[Optional[float], Optional[float]]:
    """Поиск ближайшей допустимой точки внутри кластера."""
    for dy in (-0.5, 0.0, 0.5):
        for dx in (-0.5, 0.0, 0.5):
            pt_lat = lat + dy * dlat
            pt_lon = lon + dx * dlon
            if geo.point_in_polygon(pt_lat, pt_lon, poly):
                if not any(geo.point_in_polygon(pt_lat, pt_lon, fz) for fz in forbidden or []):
                    return pt_lat, pt_lon
    return None, None


def optimize_coverage(
    task: Any,
    grid_step_m: float = 20.0,
    azimuth_step_deg: float = 45.0,
    target_coverage_ratio: float = 0.95,
    min_gain_tiles: int = 3,
    max_sectors_per_site: int = 3,
    min_quality_level: str = "good",
    progress_callback: Optional[Callable[[float, str, Optional[float]], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> List[Dict[str, Any]]:
    """Основная функция Алгоритма 1: расчёт оптимального покрытия.

    :param task: Объект PlacementTask с полями area, forbidden, tower_type, max_towers, building_data.
    :param grid_step_m: Шаг сетки дискретизации покрытия (метры).
    :param azimuth_step_deg: Дискретный шаг перебора азимута сектора (градусы).
    :param target_coverage_ratio: Целевая доля среднего покрытия (например, 0.95 для >= 95%).
    :param min_gain_tiles: Минимальный прирост новых точек для установки очередного сектора.
    :param max_sectors_per_site: Максимум секторов на одну и ту же площадку/здание.
    :param min_quality_level: Порог качества сигнала ("good" = средняя зона >= -85 дБм, "weak" = >= -105 дБм).
    :param progress_callback: Функция уведомления о прогрессе: callback(pct, msg, eta_seconds).
    :param is_cancelled: Функция проверки отмены: callback() -> bool.
    :return: Список словарей с характеристиками отобранных вышек: [{'lat', 'lon', 'azimuth', 'height', ...}].
    """
    area_poly = getattr(task, "area", [])
    if not area_poly or len(area_poly) < 3:
        return []

    # Читаем параметры из task, если они заданы
    grid_step_m = getattr(task, "grid_step_m", grid_step_m)
    azimuth_step_deg = getattr(task, "azimuth_step_deg", azimuth_step_deg)
    target_coverage_ratio = getattr(task, "target_coverage_ratio", target_coverage_ratio)
    min_gain_tiles = getattr(task, "min_gain_tiles", min_gain_tiles)
    max_sectors_per_site = getattr(task, "max_sectors_per_site", max_sectors_per_site)
    min_quality_level = getattr(task, "min_quality_level", min_quality_level)
    progress_cb = getattr(task, "progress_callback", progress_callback)
    check_cancelled = getattr(task, "is_cancelled", is_cancelled)

    if check_cancelled and check_cancelled():
        return []

    forbidden = getattr(task, "forbidden", []) or []
    tower_type = dict(getattr(task, "tower_type", {}) or {})
    max_towers = int(getattr(task, "max_towers", 10))
    building_data = getattr(task, "building_data", None)

    # Параметры радиомодели
    freq = float(tower_type.get("freq", 1800.0))
    power = float(tower_type.get("power", 20.0))
    default_h = float(tower_type.get("height", 30.0))
    wall_loss = float(tower_type.get("wall_loss", 20.0))
    tilt = float(tower_type.get("tilt", 4.0))
    beamwidth = float(tower_type.get("beamwidth", 65.0))

    # Выбор порога радиосигнала
    # THRESHOLDS в calculations.py:
    # 0: green (-55 дБм), 1: excellent (-70 дБм), 2: good (-85 дБм), 3: weak (-105 дБм)
    if min_quality_level == "excellent":
        reach_col = 1
        req_level = 3
        quality_desc = "отличного (≥ -70 дБм)"
    elif min_quality_level == "green":
        reach_col = 0
        req_level = 4
        quality_desc = "зелёного (≥ -55 дБм)"
    elif min_quality_level == "weak":
        reach_col = 3
        req_level = 1
        quality_desc = "любого (≥ -105 дБм)"
    else:  # default: "good" (средняя зона и выше, >= -85 дБм)
        reach_col = 2
        req_level = 2
        quality_desc = "среднего (≥ -85 дБм)"

    if progress_cb:
        progress_cb(5.0, f"Этап 1/5: Дискретизация целевой сетки (порог: {quality_desc})...", None)

    # --- ЭТАП 1: Дискретизация целевой зоны на сетку ---
    t_lats, t_lons, actual_step = coverage.sample_polygon(
        area_poly, forbidden=forbidden, step_m=grid_step_m, max_points=25000
    )
    total_tiles = len(t_lats)
    if total_tiles == 0:
        return []

    if check_cancelled and check_cancelled():
        return []

    # Множество ещё непокрытых индексов
    uncovered_mask = np.ones(total_tiles, dtype=bool)

    if progress_cb:
        progress_cb(12.0, "Этап 2/5: Анализ площадок на крышах зданий...", None)

    # --- ЭТАП 2: Поиск площадок-кандидатов (доминантных высот) ---
    sites = find_candidate_sites(
        area_poly=area_poly,
        forbidden_polys=forbidden,
        building_data=building_data,
        default_height=default_h,
        cluster_size_m=max(150.0, grid_step_m * 10),
    )
    if not sites:
        return []

    if check_cancelled and check_cancelled():
        return []

    # --- ЭТАП 3: Генерация направленных секторов и предрасчёт зон покрытия ---
    if beamwidth >= 330.0:
        azimuth_angles = [0.0]
    else:
        azimuth_angles = list(np.arange(0.0, 360.0, azimuth_step_deg))

    candidate_sectors: List[CandidateSector] = []
    t_start_phase3 = time.perf_counter()
    num_sites = len(sites)

    for site_idx, site in enumerate(sites):
        if check_cancelled and check_cancelled():
            return []

        for az in azimuth_angles:
            t = Tower(
                name="cand",
                lat=site.lat,
                lon=site.lon,
                freq=freq,
                power=power,
                height=site.height,
                tilt=tilt,
                wall_loss=wall_loss,
                color=[0, 0, 0, 255],
                azimuth=float(az),
                beamwidth=float(beamwidth),
                building_data=building_data,
            )

            # Берём дальность до границы выбранного качества покрытия (по умолчанию reach[:, 2] = -85 дБм, "средняя зона")
            quality_reach = t.reach[:, reach_col]

            mask_u64 = None
            if c_coverage.is_c_available():
                mask_u64, count = c_coverage.c_compute_sector_mask(
                    t_lats, t_lons, site.lat, site.lon, quality_reach
                )
                if count >= min_gain_tiles:
                    candidate_sectors.append(CandidateSector(
                        site=site,
                        azimuth=float(az),
                        beamwidth=float(beamwidth),
                        covered_indices=np.empty(0, dtype=int),
                        mask_u64=mask_u64,
                        tower_instance=t,
                    ))
            else:
                levels = coverage.level_at_points(t, t_lats, t_lons)
                covered_idx = np.where(levels >= req_level)[0]
                if len(covered_idx) >= min_gain_tiles:
                    candidate_sectors.append(CandidateSector(
                        site=site,
                        azimuth=float(az),
                        beamwidth=float(beamwidth),
                        covered_indices=covered_idx,
                        tower_instance=t,
                    ))

        # Оценка оставшегося времени (ETA)
        done_sites = site_idx + 1
        elapsed = time.perf_counter() - t_start_phase3
        sec_per_site = elapsed / max(done_sites, 1)
        remaining_sites = num_sites - done_sites
        eta_sec = sec_per_site * remaining_sites + 0.3
        pct = 15.0 + 65.0 * (done_sites / num_sites)

        if progress_cb:
            msg = f"Этап 3/5: Расчёт зон секторов (площадка {done_sites}/{num_sites}, кандидатов: {len(candidate_sectors)})..."
            progress_cb(pct, msg, eta_sec)

    if not candidate_sectors:
        return []

    if check_cancelled and check_cancelled():
        return []

    # --- ЭТАП 4: Итеративный жадный отбор и ЭТАП 5: Очистка дублей ---
    if progress_cb:
        progress_cb(82.0, f"Этап 4/5: Жадный отбор секторов (цель: ≥ {target_coverage_ratio * 100:.0f}% {quality_desc})...", 0.5)

    selected: List[CandidateSector] = []

    # 1. Если скомпилировано Си-ядро — используем сверхбыстрый битовый движок
    if c_coverage.is_c_available() and candidate_sectors[0].mask_u64 is not None:
        cand_masks = np.array([c.mask_u64 for c in candidate_sectors], dtype=np.uint64)
        site_map = {id(site): i for i, site in enumerate(sites)}
        site_ids = [site_map[id(c.site)] for c in candidate_sectors]

        sel_indices = c_coverage.c_greedy_set_cover(
            cand_masks=cand_masks,
            site_ids=site_ids,
            num_tiles=total_tiles,
            max_towers=max_towers,
            max_sectors_per_site=max_sectors_per_site,
            min_gain_tiles=min_gain_tiles,
            target_ratio=target_coverage_ratio,
        )
        selected = [candidate_sectors[i] for i in sel_indices]
    else:
        # 2. Python/NumPy fallback
        site_sector_counts: Dict[Tuple[float, float], int] = {}
        target_covered_count = int(total_tiles * target_coverage_ratio)

        while len(selected) < max_towers:
            if check_cancelled and check_cancelled():
                return []

            current_covered = total_tiles - int(uncovered_mask.sum())
            if current_covered >= target_covered_count:
                break

            best_cand: Optional[CandidateSector] = None
            max_gain: int = 0

            for cand in candidate_sectors:
                site_key = (round(cand.site.lat, 6), round(cand.site.lon, 6))
                if site_sector_counts.get(site_key, 0) >= max_sectors_per_site:
                    continue

                newly_covered = uncovered_mask[cand.covered_indices].sum()
                if newly_covered > max_gain:
                    max_gain = int(newly_covered)
                    best_cand = cand

            if best_cand is None or max_gain < min_gain_tiles:
                break

            selected.append(best_cand)
            uncovered_mask[best_cand.covered_indices] = False
            candidate_sectors.remove(best_cand)

            site_key = (round(best_cand.site.lat, 6), round(best_cand.site.lon, 6))
            site_sector_counts[site_key] = site_sector_counts.get(site_key, 0) + 1

        # Зачистка дублей в Python fallback
        if len(selected) > 1:
            if progress_cb:
                progress_cb(94.0, "Этап 5/5: Очистка избыточных дублирующих секторов...", 0.2)
            pruned_selected: List[CandidateSector] = list(selected)
            for s in reversed(selected):
                others = [o for o in pruned_selected if o is not s]
                if not others:
                    continue
                other_covered = np.zeros(total_tiles, dtype=bool)
                for o in others:
                    other_covered[o.covered_indices] = True
                if np.all(other_covered[s.covered_indices]):
                    pruned_selected.remove(s)
            selected = pruned_selected

    if progress_cb:
        progress_cb(100.0, f"Готово! Отобрано оптимальных секторов: {len(selected)}", 0.0)

    # Формируем итоговый результат
    result: List[Dict[str, Any]] = []
    for s in selected:
        tiles_count = (
            int(np.bitwise_count(s.mask_u64).sum())
            if s.mask_u64 is not None
            else len(s.covered_indices)
        )
        result.append({
            "lat": s.site.lat,
            "lon": s.site.lon,
            "height": s.site.height,
            "azimuth": s.azimuth,
            "beamwidth": s.beamwidth,
            "building_id": s.site.building_id,
            "site_name": s.site.name,
            "gain_tiles": tiles_count,
            "quality_level": min_quality_level,
        })

    return result


if __name__ == "__main__":
    # Быстрый смоук-тест автономного запуска
    print("Тестирование алгоритма 1 на тестовом полигоне...")
    # Квадратный полигон ~600x600 метров (район Москвы)
    test_poly = [
        (55.7500, 37.6100),
        (55.7550, 37.6100),
        (55.7550, 37.6200),
        (55.7500, 37.6200),
    ]

    class MockTask:
        area = test_poly
        forbidden = []
        tower_type = {"freq": 1800.0, "power": 20.0, "height": 30.0, "tilt": 4.0, "wall_loss": 20.0, "beamwidth": 65.0}
        max_towers = 5
        building_data = None

    res = optimize_coverage(MockTask(), grid_step_m=30.0, azimuth_step_deg=45.0)
    print(f"Успешно! Найдено оптимальных секторов: {len(res)}")
    for i, tw in enumerate(res, 1):
        print(f"  #{i}: lat={tw['lat']:.5f}, lon={tw['lon']:.5f}, h={tw['height']:.1f}м, az={tw['azimuth']:.0f}°, tiles={tw['gain_tiles']}")
