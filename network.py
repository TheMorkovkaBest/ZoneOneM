"""Сетевая модель (аналог Cisco Packet Tracer, но на карте): устройства, кабели, PoE, маршрутизация, ping.

Без зависимостей от Qt — полностью покрыта тестами. Физика линков:
  * медь:    скорость и длина по таблицам Cat5e/6/6A/8 (с автосогласованием вниз);
  * оптика:  форм-фактор трансивера должен подходить порту, дальность — по модулю;
  * РРЛ:     бюджет радиолинии (FSPL, газы, дождь), прямая видимость и зона Френеля по зданиям OSM;
  * PoE:     класс PSE-порта и общий бюджет коммутатора; без питания устройство и его линки «лежат».
"""
import ipaddress
import json
import math
from collections import deque
from dataclasses import dataclass, field, fields
from functools import lru_cache

import numpy as np

import geo
from calculations import GRID_RES_M
from netcatalog import (CABLES, GROUP_COLORS, L2_ROLES, L3_ROLES, MODELS, POE_TYPES, PORT_ACCEPTS,
                        RADIO_BANDS, default_model_for_tower, model_ports)

CABLE_ROUTE_FACTOR = 1.25      # кабель идёт не по прямой
CABLE_SLACK_M = 10.0           # подъём по мачте, запас, разделка
ETHERNET_EFFICIENCY = 0.94     # полезная доля скорости линка (заголовки, межкадровые интервалы)


def fmt_speed(mbps):
    if mbps >= 1000:
        g = mbps / 1000.0
        return f"{g:g}G"
    return f"{mbps:g}M"


@dataclass(frozen=True)
class Port:
    name: str
    kind: str
    mbps: float
    pse: str = None
    pd: str = None


@lru_cache(maxsize=None)
def _model_port_table(model):
    """(порты по порядку, порты по имени) — одни и те же неизменяемые объекты для всех устройств модели."""
    ports = tuple(Port(*p) for p in model_ports(model))
    return ports, {p.name: p for p in ports}


@dataclass
class Device:
    id: str
    model: str
    name: str
    lat: float
    lon: float
    tower_id: int = None
    ips: dict = field(default_factory=dict)       # порт -> "10.0.0.1/24"
    gateway: str = ""
    routes: list = field(default_factory=list)    # [{"net": "10.1.0.0/24", "via": "10.0.0.2"}]
    power_mode: str = "local"                     # local | poe
    radio: dict = field(default_factory=dict)     # для БС: tech, bw_mhz, layers, max_users
    mount_h: float = 20.0                         # высота подвеса антенны РРЛ, м

    @property
    def spec(self):
        return MODELS[self.model]

    @property
    def role(self):
        return self.spec["role"]

    def ports(self):
        return list(_model_port_table(self.model)[0])

    def port(self, name):
        return _model_port_table(self.model)[1].get(name)


@dataclass
class Link:
    id: str
    a_dev: str
    a_port: str
    b_dev: str
    b_port: str
    cable: str
    length_m: float = None        # None — по карте (авто)


@dataclass
class LinkStatus:
    state: str = "down"           # up | warn | down
    speed_mbps: float = 0.0
    reason: str = ""
    length_m: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class PingResult:
    ok: bool
    reason: str = ""
    hops: list = field(default_factory=list)      # id устройств, туда
    links: list = field(default_factory=list)     # id линков, туда
    back_hops: list = field(default_factory=list)
    back_links: list = field(default_factory=list)
    rtt_ms: float = 0.0
    src_ip: str = ""
    dst_ip: str = ""


class _VersionedDict(dict):
    """dict, который считает свои изменения (для кэша индекса портов)."""
    version = 0

    def _touch(self):
        self.version += 1

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._touch()

    def __delitem__(self, k):
        super().__delitem__(k)
        self._touch()

    def pop(self, *a):
        self._touch()
        return super().pop(*a)

    def popitem(self):
        self._touch()
        return super().popitem()

    def clear(self):
        self._touch()
        super().clear()

    def update(self, *a, **kw):
        self._touch()
        super().update(*a, **kw)

    def setdefault(self, k, v=None):
        self._touch()
        return super().setdefault(k, v)


class Network:
    def __init__(self):
        self.devices = {}
        self.links = _VersionedDict()
        self._port_index = None
        self.status = {}          # link_id -> LinkStatus
        self.dev_state = {}       # dev_id -> {"powered": bool, "reason": str, "poe_used_w": float}
        self._next_dev = 1
        self._next_link = 1
        self.partial_hops = []

    # ------------------------------------------------------------------ #
    #  Устройства
    # ------------------------------------------------------------------ #
    def add_device(self, model, name, lat, lon, **kw):
        did = f"D{self._next_dev}"
        self._next_dev += 1
        spec = MODELS[model]
        kw.setdefault("mount_h", spec.get("mount_h", 20.0))      # явное значение перекрывает умолчание модели
        dev = Device(did, model, name, lat, lon, **kw)
        self.devices[did] = dev
        return dev

    def ensure_tower(self, tower):
        """Устройство-БС для вышки (создаётся один раз, координаты и имя синхронизируются)."""
        did = f"T{tower.db_id}"
        dev = self.devices.get(did)
        model = default_model_for_tower(tower.freq, tower.height)
        if dev is None:
            spec = MODELS[model]
            dev = Device(did, model, tower.name, tower.lat, tower.lon, tower_id=tower.db_id,
                         radio=dict(spec["radio"]), mount_h=tower.height)
            self.devices[did] = dev
        elif dev.model != model and not any(did in (l.a_dev, l.b_dev) for l in self.links.values()):
            # вышку отредактировали (высота/частота) и кабелей у неё нет — меняем и класс БС,
            # иначе у бывшей макросоты остаются порты, которых у новой нет (и наоборот)
            dev.model, dev.radio, dev.ips, dev.routes, dev.gateway = model, dict(MODELS[model]["radio"]), {}, [], ""
            if dev.power_mode == "poe" and not any(p.pd for p in dev.ports()):
                dev.power_mode = "local"
        dev.name, dev.lat, dev.lon, dev.mount_h = tower.name, tower.lat, tower.lon, tower.height
        return dev

    def sync_towers(self, towers, keep_ids=()):
        """Приводит устройства-БС в соответствие со списком вышек (лишние удаляются вместе с кабелями).

        keep_ids — id вышек, которых сейчас нет на карте, но они остались в БД («Очистить карту»):
        их устройства и кабели сохраняются.
        """
        ids = {f"T{t.db_id}" for t in towers} | {f"T{i}" for i in keep_ids}
        for did in [d for d, dev in self.devices.items() if dev.tower_id is not None and d not in ids]:
            self.remove_device(did)
        for t in towers:
            self.ensure_tower(t)

    def remove_device(self, did):
        for lid in [l.id for l in self.links.values() if did in (l.a_dev, l.b_dev)]:
            self.remove_link(lid)
        self.devices.pop(did, None)
        self.dev_state.pop(did, None)

    def remove_link(self, lid):
        self.links.pop(lid, None)
        self.status.pop(lid, None)

    # ------------------------------------------------------------------ #
    #  Порты и соединения
    # ------------------------------------------------------------------ #
    def _ports_used(self):
        """{(устройство, порт): линк} — индекс вместо перебора всех линков на каждый запрос порта.

        Перестраивается при любом изменении self.links (версия словаря), в том числе в обход connect().
        """
        if self._port_index is None or self._port_index[0] != self.links.version:
            idx = {}
            for l in self.links.values():
                idx[(l.a_dev, l.a_port)] = l
                idx[(l.b_dev, l.b_port)] = l
            self._port_index = (self.links.version, idx)
        return self._port_index[1]

    def port_link(self, did, port):
        return self._ports_used().get((did, port))

    def free_ports(self, did):
        used = self._ports_used()
        return [p for p in self.devices[did].ports() if (did, p.name) not in used]

    @staticmethod
    def port_accepts_cable(dev, port, cable):
        """Подходит ли один порт под кабель (для списка выбора портов)."""
        g = cable["group"]
        if g == "copper":
            return port.kind == "RJ45"
        if g in ("fiber", "dac"):
            return cable["form"] in PORT_ACCEPTS.get(port.kind, ())
        return port.kind == "AIR" and dev.spec.get("band") == cable["band"]

    @staticmethod
    def check_connector(cable, pa, pb, dev_a, dev_b):
        """Физическая совместимость кабеля и портов. Возвращает текст ошибки или None."""
        g = cable["group"]
        if g == "copper":
            for p in (pa, pb):
                if p.kind != "RJ45":
                    return f"Кабель «витая пара» подключается только к портам RJ45, а порт {p.name} — {p.kind}"
        elif g in ("fiber", "dac"):
            for p in (pa, pb):
                if cable["form"] not in PORT_ACCEPTS.get(p.kind, ()):
                    return f"Трансивер/кабель {cable['form']} не подходит к порту {p.name} ({p.kind})"
        elif g == "radio":
            for p, d in ((pa, dev_a), (pb, dev_b)):
                if p.kind != "AIR":
                    return f"Радиолиния соединяет только радиоинтерфейсы (Air); порт {p.name} — {p.kind}"
                if d.spec.get("band") != cable["band"]:
                    return f"{d.name}: диапазон терминала не совпадает с линией «{cable['name']}»"
        return None

    def connect(self, a_dev, a_port, b_dev, b_port, cable_id):
        if a_dev == b_dev:
            return None, "Нельзя соединить устройство с самим собой"
        da, db = self.devices.get(a_dev), self.devices.get(b_dev)
        cable = CABLES.get(cable_id)
        if not (da and db and cable):
            return None, "Неизвестное устройство или тип кабеля"
        pa, pb = da.port(a_port), db.port(b_port)
        if not (pa and pb):
            return None, "Порт не найден"
        for dev, p in ((da, pa), (db, pb)):
            if self.port_link(dev.id, p.name):
                return None, f"Порт {p.name} устройства {dev.name} уже занят"
        err = self.check_connector(cable, pa, pb, da, db)
        if err:
            return None, err
        lid = f"L{self._next_link}"
        self._next_link += 1
        link = Link(lid, a_dev, a_port, b_dev, b_port, cable_id)
        self.links[lid] = link
        return link, None

    def link_length(self, link):
        if link.length_m is not None:
            return float(link.length_m)
        a, b = self.devices[link.a_dev], self.devices[link.b_dev]
        d = geo.haversine_m(a.lat, a.lon, b.lat, b.lon)
        group = CABLES[link.cable]["group"]
        if group == "radio":
            return max(d, 5.0)
        if group == "dac":                      # внутристоечные кабели: почти без запаса
            return max(d * 1.1 + 1.0, 0.5)
        return max(d * CABLE_ROUTE_FACTOR + CABLE_SLACK_M, 3.0)

    # ------------------------------------------------------------------ #
    #  Оценка линков
    # ------------------------------------------------------------------ #
    def evaluate(self, building_data=None):
        """Пересчитывает состояние всех линков и питания (PoE)."""
        self.status = {lid: self._eval_link(l, building_data) for lid, l in self.links.items()}
        self._eval_poe()

    def _eval_link(self, link, bd=None):
        da, db = self.devices[link.a_dev], self.devices[link.b_dev]
        cable = CABLES[link.cable]
        pa, pb = da.port(link.a_port), db.port(link.b_port)
        length = self.link_length(link)
        st = LinkStatus(length_m=length)
        err = self.check_connector(cable, pa, pb, da, db)
        if err:
            st.reason = err
            return st
        if cable["group"] == "copper":
            return self._eval_copper(st, cable, pa, pb, length)
        if cable["group"] in ("fiber", "dac"):
            return self._eval_optic(st, cable, pa, pb, length)
        return self._eval_radio(st, cable, da, db, pa, pb, length, bd)

    @staticmethod
    def _eval_copper(st, cable, pa, pb, length):
        common = min(pa.mbps, pb.mbps)
        for speed, max_m in cable["tiers"]:
            if speed <= common and length <= max_m:
                st.speed_mbps = float(speed)
                if speed < common:
                    st.state = "warn"
                    st.reason = (f"Согласовано {fmt_speed(speed)} вместо {fmt_speed(common)}: "
                                 f"{cable['name']} не тянет {fmt_speed(common)} на {length:.0f} м")
                else:
                    st.state = "up"
                return st
        limit = min(m for _, m in cable["tiers"])
        st.reason = f"Длина {length:.0f} м больше предела {limit} м для {cable['name']} — линк не поднимается"
        return st

    @staticmethod
    def _eval_optic(st, cable, pa, pb, length):
        if length > cable["max_m"]:
            st.reason = f"Длина {length:.0f} м больше дальности модуля ({cable['max_m']:g} м)"
            return st
        speed = min(cable["mbps"], pa.mbps, pb.mbps)
        st.speed_mbps = float(speed)
        if speed < cable["mbps"]:
            st.state = "warn"
            st.reason = f"Скорость ограничена портом: {fmt_speed(speed)} вместо {fmt_speed(cable['mbps'])}"
        else:
            st.state = "up"
        return st

    def _eval_radio(self, st, cable, da, db, pa, pb, length, bd):
        band = RADIO_BANDS[cable["band"]]
        d_km = length / 1000.0
        fspl = geo.fspl_db(length, band["freq_ghz"] * 1000.0)
        rsl = band["tx_dbm"] + 2 * band["gain_dbi"] - fspl - band["losses_db"] - band["gas_db_km"] * d_km
        los_ok, fresnel = self.clearance(bd, da, db, band["freq_ghz"])
        cap_limit = min(pa.mbps, pb.mbps)
        steps = [(s, c) for s, c in band["ladder"] if s <= rsl]
        rain_margin = rsl - band["ladder"][0][0] - band["rain_db_km"] * d_km
        st.extra = {"rsl_dbm": rsl, "fspl_db": fspl, "dist_km": d_km, "fresnel": fresnel, "los": los_ok,
                    "rain_margin_db": rain_margin, "band": band["name"]}
        if d_km > band["max_km"]:
            st.reason = f"Расстояние {d_km:.1f} км больше типовой дальности диапазона ({band['max_km']:g} км)"
            return st
        if not los_ok:
            st.reason = "Нет прямой видимости: трасса перекрыта зданием"
            return st
        if not steps:
            st.reason = f"Уровень приёма {rsl:.0f} дБм ниже порога модуляции"
            return st
        st.speed_mbps = float(min(steps[-1][1], cap_limit))
        problems = []
        if fresnel < 0.6:
            problems.append(f"зона Френеля свободна на {max(fresnel, 0) * 100:.0f}% (нужно ≥ 60%)")
        if rain_margin < 0:
            problems.append(f"запас на дождь отрицательный ({rain_margin:.0f} дБ): в ливень линк упадёт")
        st.state = "warn" if problems else "up"
        st.reason = "; ".join(problems)
        return st

    @staticmethod
    def clearance(bd, da, db, freq_ghz):
        """(видимость_ок, мин. доля 1-й зоны Френеля над зданиями). Рельеф не учитывается."""
        if bd is None:
            return True, 9.99
        dist = geo.haversine_m(da.lat, da.lon, db.lat, db.lon)
        n = int(dist // GRID_RES_M)
        if n < 2:
            return True, 9.99
        m_lon = geo.M_PER_DEG_LAT * math.cos(math.radians(da.lat))
        dx, dy = (db.lon - da.lon) * m_lon, (db.lat - da.lat) * geo.M_PER_DEG_LAT
        norm = math.hypot(dx, dy) or 1.0
        d = np.arange(1, n + 1, dtype=float) * GRID_RES_M
        heights = bd.heights_along(da.lat, da.lon, dx / norm, dy / norm, d)
        los_h = da.mount_h + (db.mount_h - da.mount_h) * d / (n * GRID_RES_M)
        wl = 0.3 / freq_ghz
        r1 = np.sqrt(wl * d * np.maximum(n * GRID_RES_M - d, 1e-6) / (n * GRID_RES_M))
        clear = np.min((los_h - heights) / np.maximum(r1, 1e-6))
        return bool(np.all(heights <= los_h)), float(clear)

    def _eval_poe(self):
        self.dev_state = {did: {"powered": True, "reason": "", "poe_used_w": 0.0} for did in self.devices}
        for did in sorted(self.devices):
            dev = self.devices[did]
            if dev.power_mode != "poe":
                continue
            draw = dev.spec["power_w"]
            reason, powered = "нет PoE-подключения (медный кабель к PSE-порту)", False
            for p in dev.ports():
                if not p.pd:
                    continue
                link = self.port_link(did, p.name)
                if not link or self.status[link.id].state == "down" or CABLES[link.cable]["group"] != "copper":
                    continue
                peer_id, peer_port = ((link.b_dev, link.b_port) if link.a_dev == did else (link.a_dev, link.a_port))
                peer = self.devices[peer_id]
                pp = peer.port(peer_port)
                if not pp.pse:
                    reason = f"порт {peer.name}/{peer_port} не поддерживает PoE"
                    continue
                avail = POE_TYPES[pp.pse][1]
                if draw > avail:
                    reason = f"PoE-порт {peer.name}/{peer_port} даёт {avail:g} Вт, а устройству нужно {draw:g} Вт"
                    continue
                cost = min(POE_TYPES[pp.pse][0], draw * 1.12)
                state = self.dev_state[peer_id]
                if state["poe_used_w"] + cost > peer.spec["poe_budget_w"] + 1e-9:
                    reason = f"превышен PoE-бюджет {peer.name} ({peer.spec['poe_budget_w']:g} Вт)"
                    continue
                state["poe_used_w"] += cost
                powered, reason = True, ""
                break
            self.dev_state[did]["powered"], self.dev_state[did]["reason"] = powered, reason
        for lid, l in self.links.items():                       # без питания линки не работают
            for did in (l.a_dev, l.b_dev):
                if not self.dev_state[did]["powered"]:
                    st = self.status[lid]
                    st.state, st.speed_mbps = "down", 0.0
                    st.reason = f"Нет питания у {self.devices[did].name}: {self.dev_state[did]['reason']}"

    # ------------------------------------------------------------------ #
    #  Подбор кабеля
    # ------------------------------------------------------------------ #
    def suggest_cable(self, a_id, b_id, building_data=None, group=None, cable_id=None):
        """Лучшая комбинация (кабель, порт A, порт B): макс. скорость, затем без предупреждений, затем дешевле.

        cable_id — тип кабеля задан пользователем, подбираются только порты.
        """
        da, db = self.devices[a_id], self.devices[b_id]
        cables = [CABLES[cable_id]] if cable_id else list(CABLES.values())

        def uniq(dev):
            seen = {}
            for p in self.free_ports(dev.id):
                seen.setdefault((p.kind, p.mbps, p.pse, p.pd), p)
            return list(seen.values())
        best = None
        for pa in uniq(da):
            for pb in uniq(db):
                for cable in cables:
                    if group and cable["group"] != group:
                        continue
                    if self.check_connector(cable, pa, pb, da, db):
                        continue
                    tmp = Link("tmp", a_id, pa.name, b_id, pb.name, cable["id"])
                    st = self._eval_link(tmp, building_data)
                    if st.state == "down":
                        continue
                    price = cable["fixed"] + cable["per_m"] * st.length_m
                    key = (-st.speed_mbps, st.state != "up", price)
                    if best is None or key < best[0]:
                        best = (key, cable["id"], pa.name, pb.name, st)
        return None if best is None else {"cable": best[1], "a_port": best[2], "b_port": best[3], "status": best[4]}

    # ------------------------------------------------------------------ #
    #  IP, маршруты, ping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_l2(dev):
        return dev.role in L2_ROLES

    @staticmethod
    def _is_l3(dev):
        return dev.role in L3_ROLES

    def _usable(self, link):
        st = self.status.get(link.id)
        return st is not None and st.state != "down"

    def segments(self):
        """L2-сегменты: список {"nodes": [(dev, port)], ...} для не-L2 портов, соединённых через коммутаторы."""
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def node(did, port):
            return ("L2", did) if self._is_l2(self.devices[did]) else (did, port)
        for l in self.links.values():
            if self._usable(l):
                ra, rb = find(node(l.a_dev, l.a_port)), find(node(l.b_dev, l.b_port))
                parent[ra] = rb
        groups = {}
        for x in list(parent):
            if x[0] != "L2":
                groups.setdefault(find(x), []).append(x)
        return [sorted(g) for g in groups.values() if len(g) >= 2]

    def autoconfigure(self):
        """Раздаёт IP на портах, поднимает маршруты между подсетями (как OSPF) и шлюзы для БС/ядра."""
        for dev in self.devices.values():
            dev.ips, dev.routes, dev.gateway = {}, [], ""
        segs = self.segments()
        seg_info = []
        for k, nodes in enumerate(segs):
            base = ipaddress.ip_network(f"10.{k // 256}.{k % 256}.0/24")
            mask = 30 if len(nodes) == 2 else 24
            net = ipaddress.ip_network(f"{base.network_address}/{mask}")
            nodes = sorted(nodes, key=lambda n: (not self._is_l3(self.devices[n[0]]), n))   # маршрутизаторы получают младшие адреса
            hosts = list(net.hosts())
            members = []
            for i, (did, port) in enumerate(nodes):
                if i >= len(hosts):
                    break
                ip = f"{hosts[i]}/{mask}"
                self.devices[did].ips[port] = ip
                members.append((did, port, str(hosts[i])))
            seg_info.append({"net": net, "members": members})
        # граф подсетей: маршрутизаторы соединяют сегменты
        by_dev = {}
        for si, s in enumerate(seg_info):
            for did, port, ip in s["members"]:
                by_dev.setdefault(did, []).append((si, ip))
        l3_in_seg = {si: [(did, ip) for did, port, ip in s["members"] if self._is_l3(self.devices[did])]
                     for si, s in enumerate(seg_info)}
        for did, dev in self.devices.items():
            if did not in by_dev:
                continue
            if self._is_l3(dev):
                first_hop = {si: None for si, _ in by_dev[did]}       # connected
                q = deque(first_hop)
                while q:
                    si = q.popleft()
                    for rid, rip in l3_in_seg[si]:
                        if rid == did:
                            continue
                        for tj, _ in by_dev.get(rid, []):
                            if tj not in first_hop:
                                first_hop[tj] = first_hop[si] or rip
                                q.append(tj)
                for sj, via in first_hop.items():
                    if via:
                        dev.routes.append({"net": str(seg_info[sj]["net"]), "via": via})
            else:
                for si, _ in by_dev[did]:
                    gws = l3_in_seg[si]
                    if gws:
                        dev.gateway = gws[0][1]
                        break
        return {"segments": len(seg_info), "devices_with_ip": len(by_dev)}

    def owner_of(self, ip):
        ip = ipaddress.ip_address(ip)
        for dev in self.devices.values():
            for port, cidr in dev.ips.items():
                if ipaddress.ip_interface(cidr).ip == ip:
                    return dev, port
        return None

    def _next_hop(self, dev, dst):
        """(порт, ip следующего перехода) или (None, причина)."""
        dst = ipaddress.ip_address(dst)
        for port, cidr in dev.ips.items():
            if dst in ipaddress.ip_interface(cidr).network:
                return port, str(dst)
        best = None
        for r in dev.routes:
            net = ipaddress.ip_network(r["net"], strict=False)
            if dst in net and (best is None or net.prefixlen > best[0].prefixlen):
                best = (net, r["via"])
        via = best[1] if best else (dev.gateway or None)
        if not via:
            return None, f"На {dev.name} нет маршрута к {dst} (нет шлюза по умолчанию)"
        for port, cidr in dev.ips.items():
            if ipaddress.ip_address(via) in ipaddress.ip_interface(cidr).network:
                return port, via
        return None, f"На {dev.name} next-hop {via} недостижим (нет интерфейса в его подсети)"

    def _l2_path(self, src_id, src_port, dst_id, dst_port):
        """Путь по линкам и коммутаторам: ([link_id], [dev_id]) либо (None, причина)."""
        link = self.port_link(src_id, src_port)
        if link is None:
            return None, f"{self.devices[src_id].name}: к порту {src_port} не подключён кабель"
        if not self._usable(link):
            return None, f"Линк {link.id} не работает: {self.status[link.id].reason}"
        # BFS с общим множеством посещённых коммутаторов: кратчайший путь, и кольца/резервные кабели
        # между коммутаторами больше не дают экспоненциального перебора всех простых путей
        q = deque([((src_id, src_port), [], [src_id])])
        visited = {src_id}
        while q:
            (did, port), lpath, dpath = q.popleft()
            l = self.port_link(did, port)
            if l is None or not self._usable(l):
                continue
            n_id, n_port = (l.b_dev, l.b_port) if (l.a_dev == did and l.a_port == port) else (l.a_dev, l.a_port)
            lp, dp = lpath + [l.id], dpath + [n_id]
            if (n_id, n_port) == (dst_id, dst_port):
                return lp, dp
            if self._is_l2(self.devices[n_id]) and n_id not in visited:
                visited.add(n_id)
                used = self._ports_used()
                for p in self.devices[n_id].ports():
                    if p.name != n_port and (n_id, p.name) in used:
                        q.append(((n_id, p.name), lp, dp))
        return None, f"Между {self.devices[src_id].name} и {self.devices[dst_id].name} нет L2-связности"

    def trace(self, src_id, dst_ip, max_hops=24):
        """Путь пакета от устройства к IP: (links, hops) или (None, причина).

        При неудаче пройденная часть пути остаётся в self.partial_hops (для анимации).
        """
        cur = self.devices[src_id]
        hops, links = [src_id], []
        self.partial_hops = [src_id]

        def fail(msg):
            self.partial_hops = list(hops)
            return None, msg
        for _ in range(max_hops):
            owner = self.owner_of(dst_ip)
            if owner is None:
                return fail(f"Адрес {dst_ip} не назначен ни одному устройству")
            if owner[0].id == cur.id:
                return links, hops
            port, nh = self._next_hop(cur, dst_ip)
            if port is None:
                return fail(nh)
            nh_owner = self.owner_of(nh)
            if nh_owner is None:
                return fail(f"Next-hop {nh} не отвечает (нет такого устройства)")
            lp, dp = self._l2_path(cur.id, port, nh_owner[0].id, nh_owner[1])
            if lp is None:
                return fail(dp)
            links += lp
            hops += dp[1:]
            cur = nh_owner[0]
            if cur.id != owner[0].id and not self._is_l3(cur):
                return fail(f"{cur.name} не маршрутизирует пакеты дальше (не L3-устройство)")
        return fail("Превышен TTL (петля маршрутизации)")

    def path_latency_ms(self, links, hops):
        t = 0.0
        for lid in links:
            l = self.links[lid]
            t += self.link_length(l) / 1000.0 * 0.005
            if CABLES[l.cable]["group"] == "radio":
                t += 0.15
        for did in hops:
            t += 0.1 if self._is_l3(self.devices[did]) else 0.03
        return t

    def ping(self, src_id, dst_id_or_ip):
        """Эхо-запрос с проверкой обратного пути. dst — id устройства (берётся первый его IP) или IP-адрес."""
        src = self.devices[src_id]
        if dst_id_or_ip in self.devices:
            dst_dev = self.devices[dst_id_or_ip]
            if not dst_dev.ips:
                return PingResult(False, f"У {dst_dev.name} нет IP-адреса (выполните автонастройку или задайте адрес)")
            dst_ip = str(ipaddress.ip_interface(next(iter(dst_dev.ips.values()))).ip)
        else:
            dst_ip = str(dst_id_or_ip)
        if not src.ips:
            return PingResult(False, f"У {src.name} нет IP-адреса (выполните автонастройку или задайте адрес)", dst_ip=dst_ip)
        links, hops = self.trace(src_id, dst_ip)
        res = PingResult(False, dst_ip=dst_ip, src_ip=str(ipaddress.ip_interface(next(iter(src.ips.values()))).ip))
        if links is None:
            res.reason = f"Запрос не дошёл: {hops}"
            res.hops = list(self.partial_hops)
            return res
        owner = self.owner_of(dst_ip)[0]
        back_links, back_hops = self.trace(owner.id, res.src_ip)
        res.hops, res.links = hops, links
        if back_links is None:
            res.reason = f"Ответ не вернулся: {back_hops}"
            res.back_hops = list(self.partial_hops)
            return res
        res.back_links, res.back_hops = back_links, back_hops
        res.rtt_ms = self.path_latency_ms(links, hops) + self.path_latency_ms(back_links, back_hops)
        res.ok = True
        return res

    # ------------------------------------------------------------------ #
    #  Сериализация, карта, спецификация
    # ------------------------------------------------------------------ #
    def to_dict(self):
        return {
            "version": 1, "next_dev": self._next_dev, "next_link": self._next_link,
            "devices": [{k: getattr(d, k) for k in ("id", "model", "name", "lat", "lon", "tower_id", "ips",
                                                    "gateway", "routes", "power_mode", "radio", "mount_h")}
                        for d in self.devices.values()],
            "links": [{k: getattr(l, k) for k in ("id", "a_dev", "a_port", "b_dev", "b_port", "cable", "length_m")}
                      for l in self.links.values()],
        }

    @classmethod
    def from_dict(cls, data):
        """Восстановление сети. Лишние/устаревшие поля игнорируются, битые линки отбрасываются."""
        net = cls()
        dev_keys = {f.name for f in fields(Device)}
        link_keys = {f.name for f in fields(Link)}
        for d in data.get("devices", []):
            if d.get("model") in MODELS and d.get("id"):
                net.devices[d["id"]] = Device(**{k: v for k, v in d.items() if k in dev_keys})
        used = set()
        for l in data.get("links", []):
            if not (l.get("cable") in CABLES and l.get("a_dev") in net.devices and l.get("b_dev") in net.devices):
                continue
            link = Link(**{k: v for k, v in l.items() if k in link_keys})
            ends = {(link.a_dev, link.a_port), (link.b_dev, link.b_port)}
            pa, pb = net.devices[link.a_dev].port(link.a_port), net.devices[link.b_dev].port(link.b_port)
            if link.a_dev == link.b_dev or not (pa and pb) or ends & used:
                continue                           # порт не существует или уже занят другим кабелем
            used |= ends
            net.links[link.id] = link

        def next_num(prefix, ids, stored):
            nums = [int(i[len(prefix):]) for i in ids if i.startswith(prefix) and i[len(prefix):].isdigit()]
            return max([int(stored or 1)] + [n + 1 for n in nums])
        # счётчики не меньше существующих id: иначе новый кабель/устройство перезапишет старый
        net._next_dev = next_num("D", net.devices, data.get("next_dev"))
        net._next_link = next_num("L", net.links, data.get("next_link"))
        return net

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_map(self, visible_towers=None):
        """Данные для отрисовки на карте.

        visible_towers — id вышек, показанных на карте; БС остальных (убраны «Очистить карту»)
        и их кабели не рисуются, чтобы по невидимым точкам нельзя было «попасть» кабелем.
        """
        def shown(d):
            return d.tower_id is None or visible_towers is None or d.tower_id in visible_towers
        devs = [{"id": d.id, "name": d.name, "role": d.role, "lat": d.lat, "lon": d.lon,
                 "tower": d.tower_id is not None, "powered": self.dev_state.get(d.id, {}).get("powered", True),
                 "model": d.spec["name"]} for d in self.devices.values() if shown(d)]
        links = []
        for l in self.links.values():
            if not (shown(self.devices[l.a_dev]) and shown(self.devices[l.b_dev])):
                continue
            st = self.status.get(l.id) or LinkStatus()
            a, b = self.devices[l.a_dev], self.devices[l.b_dev]
            cable = CABLES[l.cable]
            width = 2.0 + max(0.0, math.log10(max(st.speed_mbps, 100.0) / 100.0)) * 1.2
            links.append({"id": l.id, "a": [a.lat, a.lon], "b": [b.lat, b.lon], "state": st.state,
                          "color": GROUP_COLORS[cable["group"]], "width": round(width, 2), "group": cable["group"],
                          "label": (f"{fmt_speed(st.speed_mbps)} · {st.length_m:.0f} м" if st.state != "down"
                                    else f"✕ {st.length_m:.0f} м"), "speed": st.speed_mbps})
        return {"devices": devs, "links": links}

    def bom(self):
        """Спецификация: [(категория, наименование, кол-во, цена, сумма)] и итог."""
        rows, total = [], 0.0
        by_model = {}
        for d in self.devices.values():
            by_model[d.model] = by_model.get(d.model, 0) + 1
        for mid, n in sorted(by_model.items()):
            spec = MODELS[mid]
            rows.append(("Устройство", spec["name"], n, spec["cost"], spec["cost"] * n))
        by_cable = {}
        for l in self.links.values():
            c = CABLES[l.cable]
            ln = self.status[l.id].length_m if l.id in self.status else self.link_length(l)
            n, length, cost = by_cable.get(l.cable, (0, 0.0, 0.0))
            by_cable[l.cable] = (n + 1, length + ln, cost + c["fixed"] + c["per_m"] * ln)
        for cid, (n, length, cost) in sorted(by_cable.items()):
            rows.append(("Линия связи", f"{CABLES[cid]['name']} (всего {length:.0f} м)", n, cost / n, cost))
        total = sum(r[4] for r in rows)
        return rows, total
