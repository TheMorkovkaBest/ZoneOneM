"""Автоматический синтез транспортной сети (Backhaul Network Synthesis).

Строит иерархическую сеть для набора вышек:
1. Базовые станции (БС) на вышках (Device role='tower').
2. Маршрутизаторы доступа / агрегации (Cell Site Routers 'rt_csr' или L3-коммутаторы 'sw_l3_agg10').
3. Центральный узел ядра сети (Core 5GC/EPC 'core_5gc') с выходом в Интернет.
4. Прокладка кабелей (10G/25G/100G оптика f_10g_lr, f_25g_lr, f_100g_lr4 или уличная медь).
5. Автоматическая IP-адресация и генерация маршрутов (network.autoconfigure).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import geo
from catalog.netcatalog import MODELS
from networking.network import Network


def build_backhaul_network(
    network: Network,
    towers: Sequence[Any],
    building_data: Optional[Any] = None,
    max_towers_per_router: int = 4,
    use_optical_backhaul: bool = True,
) -> Dict[str, Any]:
    """Автоматически создаёт и коммутирует транспортную сеть для заданных вышек.

    :param network: Экземпляр Network для добавления устройств и линков.
    :param towers: Список объектов Tower.
    :param building_data: Данные о зданиях для проверки профиля трасс.
    :param max_towers_per_router: Максимальное число вышек на один маршрутизатор агрегации.
    :param use_optical_backhaul: Использовать оптические трансиверы Single-Mode 10G/25G.
    :return: Словарь со статистикой построенной сети: {'routers': ..., 'core': ..., 'links': ...}
    """
    if not towers:
        return {"status": "empty", "message": "Нет вышек для построения сети", "devices": 0, "links": 0}

    # 1. Синхронизируем базовые станции вышек в сети
    bs_devices = []
    for tw in towers:
        bs_dev = network.ensure_tower(tw)
        bs_devices.append(bs_dev)

    # 2. Вычисляем географический центр для размещения ядра сети (Core 5GC)
    lats = [tw.lat for tw in towers]
    lons = [tw.lon for tw in towers]
    c_lat = sum(lats) / len(lats)
    c_lon = sum(lons) / len(lons)

    # Ищем, есть ли уже ядро сети
    core_dev = next((d for d in network.devices.values() if d.role == "core"), None)
    if core_dev is None:
        # Размещаем ядро сети в центре масс вышек (или с небольшим смещением ~50м к северу)
        core_dev = network.add_device(
            model="core_5gc",
            name="Ядро сети (5GC)",
            lat=c_lat + 0.0005,
            lon=c_lon,
        )

    # 3. Кластеризуем вышки для подключения к маршрутизаторам агрегации (CSR)
    # Если вышек <= 3, достаточно одного центрального маршрутизатора
    # Если вышек больше, группируем близко расположенные вышки
    towers_list = list(towers)
    clusters: List[List[Any]] = []
    visited = set()

    for i, t1 in enumerate(towers_list):
        if i in visited:
            continue
        cluster = [t1]
        visited.add(i)
        for j, t2 in enumerate(towers_list):
            if j in visited:
                continue
            dist_m = geo.haversine_m(t1.lat, t1.lon, t2.lat, t2.lon)
            if dist_m <= 450.0 and len(cluster) < max_towers_per_router:
                cluster.append(t2)
                visited.add(j)
        clusters.append(cluster)

    created_routers = []
    created_links = []

    # 4. Создаём маршрутизаторы агрегации (rt_csr) и подключаем вышки
    for c_idx, cluster in enumerate(clusters, 1):
        # Координаты маршрутизатора: центроид кластера или позиция первой (наивысшей) вышки
        r_tower = max(cluster, key=lambda t: getattr(t, "height", 30.0))
        r_name = f"Маршрутизатор CSR-{c_idx}" if len(clusters) > 1 else "Маршрутизатор CSR-1"
        
        # Проверяем, нет ли уже маршрутизатора рядом
        existing_r = next(
            (d for d in network.devices.values()
             if d.role == "router" and geo.haversine_m(d.lat, d.lon, r_tower.lat, r_tower.lon) < 30.0),
            None
        )
        if existing_r:
            router_dev = existing_r
        else:
            router_dev = network.add_device(
                model="rt_csr",
                name=r_name,
                lat=r_tower.lat,
                lon=r_tower.lon,
                mount_h=max(20.0, getattr(r_tower, "height", 20.0)),
            )
            created_routers.append(router_dev)

        # Подключаем каждую базовую станцию кластера к этому маршрутизатору
        for tw in cluster:
            tw_dev = network.ensure_tower(tw)
            # Проверяем, не подключена ли уже вышка
            already_connected = any(
                l.a_dev == tw_dev.id or l.b_dev == tw_dev.id for l in network.links.values()
            )
            if not already_connected:
                # Подбираем кабель между БС и маршрутизатором
                res = network.suggest_cable(tw_dev.id, router_dev.id, building_data=building_data)
                if res and res.get("cable"):
                    cable_id = res["cable"]
                    link, err = network.connect(tw_dev.id, res["a_port"], router_dev.id, res["b_port"], cable_id)
                    if link:
                        created_links.append(link)
                else:
                    # Резервный выбор: оптика 10G или Cat6A уличная
                    cable_id = "f_10g_lr" if use_optical_backhaul else "cat6a_out"
                    free_bs = network.free_ports(tw_dev.id)
                    free_r = network.free_ports(router_dev.id)
                    p_bs = next((p.name for p in free_bs if p.kind in ("SFP+", "SFP28", "RJ45")), None)
                    p_r = next((p.name for p in free_r if p.kind in ("SFP+", "SFP28", "RJ45")), None)
                    if p_bs and p_r:
                        link, err = network.connect(tw_dev.id, p_bs, router_dev.id, p_r, cable_id)
                        if link:
                            created_links.append(link)

        # 5. Подключаем маршрутизатор агрегации к ядру сети (Core 5GC)
        # Проверяем, не соединён ли уже маршрутизатор с ядром
        connected_to_core = any(
            (l.a_dev == router_dev.id and l.b_dev == core_dev.id) or
            (l.b_dev == router_dev.id and l.a_dev == core_dev.id)
            for l in network.links.values()
        )
        if not connected_to_core:
            res_core = network.suggest_cable(router_dev.id, core_dev.id, building_data=building_data)
            if res_core and res_core.get("cable"):
                cable_id = res_core["cable"]
                link, err = network.connect(router_dev.id, res_core["a_port"], core_dev.id, res_core["b_port"], cable_id)
                if link:
                    created_links.append(link)
            else:
                # Резервная магистраль 25G / 10G
                free_r = network.free_ports(router_dev.id)
                free_c = network.free_ports(core_dev.id)
                # Ищем SFP28 (25G) или SFP+ (10G)
                pr = next((p.name for p in free_r if p.kind in ("SFP28", "SFP+")), None)
                pc = next((p.name for p in free_c if p.kind in ("SFP28", "QSFP28", "SFP+")), None)
                if pr and pc:
                    link, err = network.connect(router_dev.id, pr, core_dev.id, pc, "f_25g_lr")
                    if link:
                        created_links.append(link)

    # 6. Оценка физики линков и автонастройка адресов и маршрутов
    network.evaluate(building_data)
    network.autoconfigure()

    return {
        "status": "ok",
        "core_id": core_dev.id,
        "routers": len(created_routers),
        "links": len(created_links),
        "total_devices": len(network.devices),
        "total_links": len(network.links),
    }
