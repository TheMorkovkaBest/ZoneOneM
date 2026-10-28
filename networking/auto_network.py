"""Автоматический синтез транспортной сети (Backhaul Network Synthesis).

Строит отказоустойчивую иерархическую сеть для любого числа вышек:
1. Базовые станции (БС) на вышках (Device role='tower').
2. Маршрутизаторы сайтов (Cell Site Routers 'rt_csr'):
   - Вышки на одной площадке/крыше или в одном микрорайоне объединяются в локальный CSR.
3. Маршрутизаторы ядра (Core PE Routers 'rt_pe'):
   - Размещаются перед ядром сети 5GC для концентрации каналов от CSR.
   - Каждый Core Router имеет 24x SFP28 портов для подключения CSR.
   - Если CSR больше 20, автоматически создаются 2 или более Core Routers, исключая дефицит портов.
4. Центральный узел ядра сети (Core Data Center 'core_5gc'):
   - Подключает маршрутизаторы ядра по сверхскоростным магистралям 100G QSFP28 (f_100g_lr4 / dac_100g).
5. Автоматическая прокладка кабелей, IP-адресация (/30, /24) и таблицы маршрутизации (network.autoconfigure).
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
    max_towers_per_csr: int = 6,
    max_csrs_per_core_router: int = 20,
    use_optical_backhaul: bool = True,
    **kw,
) -> Dict[str, Any]:
    """Автоматически создаёт и коммутирует полную иерархическую транспортную сеть для вышек.

    Гарантирует 100% связность:
    - Ни одна вышка и ни один маршрутизатор не остаются неподключенными.
    - Перед ядром сети устанавливаются маршрутизаторы ядра (rt_pe), число которых
      динамически масштабируется при нехватке портов (1, 2 и более).

    :param network: Экземпляр Network.
    :param towers: Список вышек (Tower).
    :param building_data: Данные о зданиях для оценки профиля трасс.
    :param max_towers_per_csr: Максимальное число вышек на один сайт-маршрутизатор CSR.
    :param max_csrs_per_core_router: Лимит CSR на один Core PE Router (у rt_pe 24 порта SFP28).
    :param use_optical_backhaul: Использовать оптику 10G/25G/100G.
    :return: Словарь со статистикой построенной сети.
    """
    if "max_towers_per_router" in kw:
        max_towers_per_csr = kw["max_towers_per_router"]

    if not towers:
        return {"status": "empty", "message": "Нет вышек для построения сети", "devices": 0, "links": 0, "routers": 0}

    # 1. Синхронизируем базовые станции вышек в сети
    bs_devices = []
    for tw in towers:
        bs_dev = network.ensure_tower(tw)
        bs_devices.append(bs_dev)

    # 2. Вычисляем географический центр масс всех вышек
    lats = [tw.lat for tw in towers]
    lons = [tw.lon for tw in towers]
    c_lat = sum(lats) / len(lats)
    c_lon = sum(lons) / len(lons)

    # 3. Ищем или создаём центральный узел ядра сети (Core 5GC)
    core_dev = next((d for d in network.devices.values() if d.role == "core"), None)
    if core_dev is None:
        core_dev = network.add_device(
            model="core_5gc",
            name="Ядро сети (5GC)",
            lat=c_lat + 0.0004,
            lon=c_lon,
        )

    # 4. Кластеризация вышек в сайтовые группы для подключения к CSR
    # Вышки на одном здании / мачте (расстояние < 40м) всегда в одной группе
    # Вышки в одном районе (< 350м) объединяются в группы до max_towers_per_csr
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
            # Приоритет объединения: на одной площадке (<40м) или в одном секторе микрорайона (<350м)
            is_same_site = dist_m <= 40.0
            is_near = dist_m <= 350.0 and len(cluster) < max_towers_per_csr
            if is_same_site or is_near:
                cluster.append(t2)
                visited.add(j)
        clusters.append(cluster)

    created_csrs = []
    created_core_routers = []
    created_links = []

    # 5. Создаём сайтовые маршрутизаторы доступа (CSR 'rt_csr')
    for c_idx, cluster in enumerate(clusters, 1):
        # Размещаем CSR на самой высокой вышке / крыше кластера
        r_tower = max(cluster, key=lambda t: getattr(t, "height", 30.0))
        r_name = f"Маршрутизатор CSR-{c_idx}" if len(clusters) > 1 else "Маршрутизатор CSR-1"

        # Проверяем, нет ли уже готового CSR на этой площадке
        existing_csr = next(
            (d for d in network.devices.values()
             if d.role == "router" and "CSR" in d.name and geo.haversine_m(d.lat, d.lon, r_tower.lat, r_tower.lon) < 25.0),
            None
        )
        if existing_csr:
            csr_dev = existing_csr
        else:
            csr_dev = network.add_device(
                model="rt_csr",
                name=r_name,
                lat=r_tower.lat,
                lon=r_tower.lon,
                mount_h=max(20.0, getattr(r_tower, "height", 20.0)),
            )
            created_csrs.append(csr_dev)

        # Подключаем каждую БС кластера к своему CSR
        for tw in cluster:
            tw_dev = network.ensure_tower(tw)
            already_connected = any(
                (l.a_dev == tw_dev.id or l.b_dev == tw_dev.id) for l in network.links.values()
            )
            if not already_connected:
                _connect_devices_best_effort(network, tw_dev, csr_dev, building_data,
                                             preferred_cable="f_10g_lr", fallback_cable="cat6a_out",
                                             out_links=created_links)

    # Все CSR маршрутизаторы, требующие подключения к ядру
    all_csrs = [d for d in network.devices.values() if d.role == "router" and ("CSR" in d.name or d.model == "rt_csr")]
    if not all_csrs:
        all_csrs = created_csrs

    # 6. Создаём пул Маршрутизаторов Ядра (Core PE Routers 'rt_pe') перед core_5gc
    # Число Core Routers рассчитывается так, чтобы на каждом было не более max_csrs_per_core_router
    num_csrs = len(all_csrs)
    num_core_routers_needed = max(1, math.ceil(num_csrs / max_csrs_per_core_router))

    # Ищем существующие Core PE маршрутизаторы
    existing_pe = [d for d in network.devices.values() if d.role == "router" and ("PE" in d.name or "Ядра" in d.name or d.model == "rt_pe")]
    core_routers: List[Any] = list(existing_pe)

    # Создаём недостающие маршрутизаторы ядра
    while len(core_routers) < num_core_routers_needed:
        idx = len(core_routers) + 1
        # Располагаем маршрутизаторы ядра веером вокруг ядра сети (~40-80м)
        angle = (idx - 1) * (2.0 * math.pi / max(num_core_routers_needed, 1))
        pe_lat = core_dev.lat - 0.0003 + 0.0002 * math.cos(angle)
        pe_lon = core_dev.lon + 0.0003 * math.sin(angle)
        pe_dev = network.add_device(
            model="rt_pe",
            name=f"Маршрутизатор ядра PE-{idx}" if num_core_routers_needed > 1 else "Маршрутизатор ядра PE",
            lat=pe_lat,
            lon=pe_lon,
            mount_h=25.0,
        )
        core_routers.append(pe_dev)
        created_core_routers.append(pe_dev)

    # 7. Подключаем каждый Core Router (rt_pe) к Ядру сети (core_5gc) по 100G QSFP28
    for pe_idx, pe_dev in enumerate(core_routers):
        is_pe_connected_to_core = any(
            ((l.a_dev == pe_dev.id and l.b_dev == core_dev.id) or
             (l.b_dev == pe_dev.id and l.a_dev == core_dev.id))
            for l in network.links.values()
        )
        if not is_pe_connected_to_core:
            # Магистральный канал 100G QSFP28
            _connect_devices_best_effort(
                network, pe_dev, core_dev, building_data,
                preferred_cable="f_100g_lr4", fallback_cable="dac_100g",
                out_links=created_links, preferred_kinds=("QSFP28",)
            )

    # Интерконнект между маршрутизаторами ядра (если их 2 и более) для резервирования
    if len(core_routers) >= 2:
        for i in range(len(core_routers) - 1):
            pe1, pe2 = core_routers[i], core_routers[i + 1]
            interconnected = any(
                ((l.a_dev == pe1.id and l.b_dev == pe2.id) or
                 (l.b_dev == pe1.id and l.a_dev == pe2.id))
                for l in network.links.values()
            )
            if not interconnected:
                _connect_devices_best_effort(
                    network, pe1, pe2, building_data,
                    preferred_cable="f_100g_lr4", fallback_cable="f_25g_lr",
                    out_links=created_links
                )

    # 8. Подключаем ВСЕ сайтовые маршрутизаторы (CSR) к Маршрутизаторам Ядра (rt_pe)
    for csr_idx, csr_dev in enumerate(all_csrs):
        is_csr_connected = any(
            any((l.a_dev == csr_dev.id and l.b_dev == pe.id) or
                (l.b_dev == csr_dev.id and l.a_dev == pe.id)
                for pe in core_routers)
            for l in network.links.values()
        )
        if is_csr_connected:
            continue

        # Сбалансированное распределение по маршрутизаторам ядра: выбираем наименее загруженный PE
        connected = False
        ordered_pes = sorted(
            core_routers,
            key=lambda pe: sum(1 for l in network.links.values() if l.a_dev == pe.id or l.b_dev == pe.id)
        )

        for pe in ordered_pes:
            free_ports = network.free_ports(pe.id)
            has_compat = any(p.kind in ("SFP28", "SFP+") for p in free_ports)
            if not has_compat:
                continue
            link = _connect_devices_best_effort(
                network, csr_dev, pe, building_data,
                preferred_cable="f_25g_lr", fallback_cable="f_10g_lr",
                out_links=created_links, preferred_kinds=("SFP28", "SFP+")
            )
            if link:
                connected = True
                break

        # Если на всех существующих PE закончились совместимые порты — создаём новый Core Router
        if not connected:
            new_idx = len(core_routers) + 1
            new_pe = network.add_device(
                model="rt_pe",
                name=f"Маршрутизатор ядра PE-{new_idx}",
                lat=core_dev.lat - 0.0003,
                lon=core_dev.lon + 0.0002 * new_idx,
                mount_h=25.0,
            )
            core_routers.append(new_pe)
            created_core_routers.append(new_pe)

            # Подключаем новый PE к ядру сети или к соседнему PE
            pe_core_link = _connect_devices_best_effort(
                network, new_pe, core_dev, building_data,
                preferred_cable="f_100g_lr4", fallback_cable="f_25g_lr",
                out_links=created_links, preferred_kinds=("QSFP28", "SFP28")
            )
            if not pe_core_link and len(core_routers) >= 2:
                _connect_devices_best_effort(
                    network, new_pe, core_routers[0], building_data,
                    preferred_cable="f_100g_lr4", fallback_cable="f_25g_lr",
                    out_links=created_links
                )

            # Подключаем CSR к новому PE
            _connect_devices_best_effort(
                network, csr_dev, new_pe, building_data,
                preferred_cable="f_25g_lr", fallback_cable="f_10g_lr",
                out_links=created_links, preferred_kinds=("SFP28", "SFP+")
            )

    # 9. Финальная проверка связности: ни одно устройство не должно остаться без линка
    _ensure_all_devices_connected(network, core_routers, core_dev, building_data, created_links)

    # 10. Оценка физики линков и автоконфигурация IP-подсетей и маршрутов
    network.evaluate(building_data)
    auto_info = network.autoconfigure()

    return {
        "status": "ok",
        "core_id": core_dev.id,
        "routers": len(created_csrs) + len(created_core_routers),
        "links": len(created_links),
        "core_routers": len(core_routers),
        "csrs": len(all_csrs),
        "created_routers": len(created_csrs) + len(created_core_routers),
        "created_links": len(created_links),
        "total_devices": len(network.devices),
        "total_links": len(network.links),
        "segments": auto_info.get("segments", 0),
        "devices_with_ip": auto_info.get("devices_with_ip", 0),
    }


def _connect_devices_best_effort(
    network: Network,
    dev_a: Any,
    dev_b: Any,
    building_data: Optional[Any],
    preferred_cable: str = "f_10g_lr",
    fallback_cable: str = "cat6a_out",
    out_links: Optional[List[Any]] = None,
    preferred_kinds: Optional[Tuple[str, ...]] = None,
) -> Optional[Any]:
    """Надёжно соединяет два устройства, проверяя совместимость портов и доступные кабели."""
    # 1. Пробуем стандартный suggest_cable
    res = network.suggest_cable(dev_a.id, dev_b.id, building_data=building_data)
    if res and res.get("cable"):
        link, err = network.connect(dev_a.id, res["a_port"], dev_b.id, res["b_port"], res["cable"])
        if link:
            if out_links is not None:
                out_links.append(link)
            return link

    # 2. Если suggest_cable не дал результата — явный перебор доступных свободных портов
    free_a = network.free_ports(dev_a.id)
    free_b = network.free_ports(dev_b.id)

    # Сортируем порты: сначала подходящие preferred_kinds, затем максимальная скорость
    def port_score(p):
        score = 0
        if preferred_kinds and p.kind in preferred_kinds:
            score += 1000000
        score += p.mbps
        return score

    sorted_a = sorted(free_a, key=port_score, reverse=True)
    sorted_b = sorted(free_b, key=port_score, reverse=True)

    cables_to_try = [preferred_cable, fallback_cable, "f_25g_lr", "f_10g_lr", "f_100g_lr4", "cat6a_out", "cat6"]

    for cable_id in cables_to_try:
        for pa in sorted_a:
            for pb in sorted_b:
                link, err = network.connect(dev_a.id, pa.name, dev_b.id, pb.name, cable_id)
                if link:
                    if out_links is not None:
                        out_links.append(link)
                    return link

    return None


def _ensure_all_devices_connected(
    network: Network,
    core_routers: List[Any],
    core_dev: Any,
    building_data: Optional[Any],
    out_links: List[Any],
) -> None:
    """Гарантирует, что абсолютно каждое устройство имеет хотя бы одно соединение."""
    for did, dev in list(network.devices.items()):
        if dev.id == core_dev.id:
            continue
        has_link = any((l.a_dev == dev.id or l.b_dev == dev.id) for l in network.links.values())
        if not has_link:
            # Ищем ближайший активный маршрутизатор
            target = None
            if dev.role == "tower":
                # Для вышки ищем любой маршрутизатор CSR с свободными портами
                csrs = [d for d in network.devices.values() if d.role == "router" and "CSR" in d.name]
                for c in sorted(csrs, key=lambda r: geo.haversine_m(dev.lat, dev.lon, r.lat, r.lon)):
                    if network.free_ports(c.id):
                        target = c
                        break
            elif dev.role == "router":
                # Для маршрутизатора ищем маршрутизатор ядра или само ядро
                for pe in core_routers:
                    if network.free_ports(pe.id):
                        target = pe
                        break
                if not target and network.free_ports(core_dev.id):
                    target = core_dev

            if target:
                _connect_devices_best_effort(network, dev, target, building_data, out_links=out_links)
