"""Опрос устройств сети по SNMP (модель агента), без зависимостей от Qt.

Каждое устройство проекта (БС, коммутатор, маршрутизатор, РРЛ, ядро) отвечает на snmpwalk
так, как ответил бы реальный агент SNMPv2c: группа system, ifTable/ifXTable (скорость,
состояние, счётчики октетов, ошибки), ipAddrTable, таблица маршрутов, соседи LLDP,
PoE (POWER-ETHERNET-MIB) у коммутаторов и радиопараметры у базовых станций.

Значения берутся из сетевой модели (network.py): состояние линков, согласованная скорость,
IP-адреса после «Авто-IP», PoE-бюджет. Счётчики трафика растут со временем работы агента
пропорционально загрузке линка (из теста нагрузки, если он запускался).

Доступность честная: менеджер SNMP (NMS) стоит на ядре сети, и запрос идёт по смоделированной
IP-сети (тот же ping, что и в режиме PDU). Нет маршрута — «Timeout: No Response», данные тогда
показываются как при локальном подключении к порту управления (craft).
"""
import hashlib
import ipaddress
import time
from dataclasses import dataclass, field

import netcatalog as NC
from network import fmt_speed

ENTERPRISE = "1.3.6.1.4.1.64999"          # условный номер предприятия ZoneM (для собственных объектов)
COMMUNITY = "public"
IF_TYPES = {"AIR": (188, "radioMAC")}     # остальные порты — ethernetCsmacd(6)
STATUS = {1: "up", 2: "down", 7: "lowerLayerDown"}
_OID = {
    "sysDescr": "1.3.6.1.2.1.1.1.0", "sysObjectID": "1.3.6.1.2.1.1.2.0", "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysContact": "1.3.6.1.2.1.1.4.0", "sysName": "1.3.6.1.2.1.1.5.0", "sysLocation": "1.3.6.1.2.1.1.6.0",
    "sysServices": "1.3.6.1.2.1.1.7.0",
}
IF_COLUMNS = (("ifIndex", "1.3.6.1.2.1.2.2.1.1"), ("ifDescr", "1.3.6.1.2.1.2.2.1.2"), ("ifType", "1.3.6.1.2.1.2.2.1.3"),
              ("ifMtu", "1.3.6.1.2.1.2.2.1.4"), ("ifPhysAddress", "1.3.6.1.2.1.2.2.1.6"),
              ("ifAdminStatus", "1.3.6.1.2.1.2.2.1.7"), ("ifOperStatus", "1.3.6.1.2.1.2.2.1.8"),
              ("ifInErrors", "1.3.6.1.2.1.2.2.1.14"), ("ifHCInOctets", "1.3.6.1.2.1.31.1.1.1.6"),
              ("ifHCOutOctets", "1.3.6.1.2.1.31.1.1.1.10"), ("ifHighSpeed", "1.3.6.1.2.1.31.1.1.1.15"),
              ("ifAlias", "1.3.6.1.2.1.31.1.1.1.18"))


@dataclass
class SnmpResult:
    device_id: str
    name: str
    ip: str = ""                     # адрес агента (пусто — нет IP)
    manager: str = ""                # кто опрашивает (NMS на ядре) или «локально»
    transport: str = "local"         # network | local
    reachable: bool = True           # агент ответил (по сети или локально)
    status: str = ""                 # строка состояния для заголовка
    rtt_ms: float = 0.0
    path: list = field(default_factory=list)          # имена узлов по пути запроса
    uptime_s: float = 0.0
    system: list = field(default_factory=list)        # [(oid, объект, значение)]
    interfaces: list = field(default_factory=list)    # [dict] строки ifTable
    ip_addrs: list = field(default_factory=list)      # [dict]
    routes: list = field(default_factory=list)        # [dict]
    neighbors: list = field(default_factory=list)     # [dict] LLDP
    extra_title: str = ""
    extra: list = field(default_factory=list)         # [(oid, объект, значение)] — радио БС / PoE

    @property
    def command(self):
        return f"snmpwalk -v2c -c {COMMUNITY} {self.ip or '<нет IP>'}"

    def walk_text(self):
        """Вывод в стиле net-snmp (snmpwalk), чтобы можно было скопировать."""
        out = [f"$ {self.command}"]
        if not self.reachable:
            out.append(f"Timeout: No Response from {self.ip or self.name}.")
            return "\n".join(out)
        for oid, name, val in self.system:
            out.append(f"{name} ({oid}) = {val}")
        for row in self.interfaces:
            i = row["ifIndex"]
            for col, oid in IF_COLUMNS:
                out.append(f"IF-MIB::{col}.{i} ({oid}.{i}) = {row[col]}")
        for r in self.ip_addrs:
            out.append(f"IP-MIB::ipAdEntAddr.{r['addr']} = IpAddress: {r['addr']}  ifIndex {r['ifIndex']}, "
                       f"mask {r['mask']}")
        for r in self.routes:
            out.append(f"IP-FORWARD-MIB::ipCidrRouteNextHop.{r['dest']} = IpAddress: {r['next_hop']} ({r['proto']})")
        for r in self.neighbors:
            out.append(f"LLDP-MIB::lldpRemSysName.{r['local_port']} = STRING: {r['sys_name']}  "
                       f"порт {r['port_id']}, {r['sys_desc']}")
        for oid, name, val in self.extra:
            out.append(f"{name} ({oid}) = {val}")
        return "\n".join(out)


def _h(*parts):
    return int.from_bytes(hashlib.md5("|".join(map(str, parts)).encode()).digest()[:8], "big")


def mac_of(dev_id, port):
    b = _h("mac", dev_id, port).to_bytes(8, "big")[:4]
    return "02:5a:" + ":".join(f"{x:02x}" for x in b)        # локально администрируемый MAC


def fmt_ticks(sec):
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"Timeticks: ({int(sec * 100)}) {d} д {h:02d}:{m:02d}:{s:02d}"


def fmt_octets(n):
    for unit, k in (("ТБ", 1e12), ("ГБ", 1e9), ("МБ", 1e6), ("КБ", 1e3)):
        if n >= k:
            return f"{n / k:.2f} {unit}"
    return f"{n} Б"


def _first_ip(dev):
    for cidr in dev.ips.values():
        return str(ipaddress.ip_interface(cidr).ip)
    return ""


def _boot_time(net, dev_id):
    """Агент «загрузился» за 1–40 суток до запуска программы (детерминированно по id)."""
    return getattr(net, "boot_time", time.time()) - 86400.0 * (1 + _h("boot", dev_id) % 40) - _h("s", dev_id) % 86400


def poll(net, dev_id, link_util=None, tower=None, now=None):
    """snmpwalk по устройству dev_id. link_util — {link_id: загрузка 0..1} (из теста нагрузки) или None."""
    dev = net.devices[dev_id]
    now = time.time() if now is None else now
    if not net.status and net.links:
        net.evaluate()
    res = SnmpResult(dev_id, dev.name, ip=_first_ip(dev))
    _connect(net, dev, res)
    if not res.reachable:
        return res
    res.uptime_s = max(now - _boot_time(net, dev_id), 1.0)
    spec = dev.spec
    model_idx = list(NC.MODELS).index(dev.model) + 1
    res.system = [
        (_OID["sysDescr"], "SNMPv2-MIB::sysDescr.0", f"STRING: {spec['name']} — {spec['example']}"),
        (_OID["sysObjectID"], "SNMPv2-MIB::sysObjectID.0", f"OID: {ENTERPRISE}.1.{model_idx}"),
        (_OID["sysUpTime"], "DISMAN-EVENT-MIB::sysUpTimeInstance", fmt_ticks(res.uptime_s)),
        (_OID["sysContact"], "SNMPv2-MIB::sysContact.0", "STRING: noc@zonem.local"),
        (_OID["sysName"], "SNMPv2-MIB::sysName.0", f"STRING: {dev.name}"),
        (_OID["sysLocation"], "SNMPv2-MIB::sysLocation.0", f"STRING: {dev.lat:.6f}, {dev.lon:.6f}"),
        (_OID["sysServices"], "SNMPv2-MIB::sysServices.0",
         f"INTEGER: {78 if dev.role in NC.L3_ROLES or dev.role == 'core' else 6}"),
    ]
    port_index = {}
    for i, p in enumerate(dev.ports(), 1):
        port_index[p.name] = i
        res.interfaces.append(_if_row(net, dev, p, i, res.uptime_s, link_util))
    for port, cidr in dev.ips.items():
        itf = ipaddress.ip_interface(cidr)
        res.ip_addrs.append({"addr": str(itf.ip), "ifIndex": port_index.get(port, 0), "port": port,
                             "mask": str(itf.network.netmask), "prefix": itf.network.prefixlen})
    for port, cidr in dev.ips.items():
        net_ = ipaddress.ip_interface(cidr).network
        res.routes.append({"dest": str(net_), "next_hop": "0.0.0.0", "port": port, "proto": "local(2)"})
    for r in dev.routes:
        res.routes.append({"dest": r["net"], "next_hop": r["via"], "port": "", "proto": "netmgmt(3)"})
    if dev.gateway:
        res.routes.append({"dest": "0.0.0.0/0", "next_hop": dev.gateway, "port": "", "proto": "netmgmt(3)"})
    for p in dev.ports():
        l = net.port_link(dev.id, p.name)
        if not l or net.status.get(l.id) is None or net.status[l.id].state == "down":
            continue
        peer_id, peer_port = (l.b_dev, l.b_port) if l.a_dev == dev.id else (l.a_dev, l.a_port)
        peer = net.devices[peer_id]
        res.neighbors.append({"local_port": p.name, "sys_name": peer.name, "port_id": peer_port,
                              "sys_desc": peer.spec["name"], "man_addr": _first_ip(peer),
                              "cable": NC.CABLES[l.cable]["name"]})
    if dev.role == "tower":
        res.extra_title = "Радио БС"
        res.extra = _radio_rows(dev, tower)
    elif dev.spec.get("poe_budget_w"):
        res.extra_title = "PoE"
        res.extra = _poe_rows(net, dev)
    return res


def _connect(net, dev, res):
    st = net.dev_state.get(dev.id, {})
    if not st.get("powered", True):
        res.reachable, res.transport = False, "network"
        res.status = f"Нет ответа: у устройства нет питания ({st.get('reason', '')})"
        return
    cores = [d for d in net.devices.values() if d.role == "core"]
    if not cores:
        res.manager = "ноутбук инженера (craft-порт)"
        res.status = "Локальное подключение к порту управления: в проекте нет ядра сети/NMS"
        return
    mgr = cores[0]
    res.manager = f"NMS на {mgr.name}" + (f" ({_first_ip(mgr)})" if mgr.ips else "")
    if mgr.id == dev.id:
        res.transport, res.status = "network", "Агент на самом сервере NMS (localhost)"
        return
    if not dev.ips or not mgr.ips:
        who = dev.name if not dev.ips else mgr.name
        res.manager = "ноутбук инженера (craft-порт)"
        res.status = (f"У {who} нет IP-адреса — опрос по сети невозможен (выполните «Авто-IP»); "
                      "показаны данные с локального порта управления")
        return
    ping = net.ping(mgr.id, dev.id)
    if ping.ok:
        res.transport, res.rtt_ms = "network", ping.rtt_ms
        res.path = [net.devices[i].name for i in ping.hops]
        res.status = f"Ответ по сети от {res.ip}: RTT {ping.rtt_ms:.2f} мс, {len(ping.hops) - 1} перех."
    else:
        res.manager = "ноутбук инженера (craft-порт)"
        res.status = (f"Timeout: No Response from {res.ip} ({ping.reason}); "
                      "показаны данные с локального порта управления")


def _if_row(net, dev, p, idx, uptime_s, link_util):
    l = net.port_link(dev.id, p.name)
    st = net.status.get(l.id) if l else None
    up = st is not None and st.state != "down"
    oper = 1 if up else (7 if (l and st is not None) else 2)
    speed = (st.speed_mbps if up else p.mbps) or p.mbps
    util = 0.0
    if up:
        util = (link_util or {}).get(l.id)
        if util is None:                                       # фоновый трафик 2–20% (детерминированно)
            util = 0.02 + (_h("u", l.id) % 1800) / 10000.0
    bps = speed * 1e6 / 8.0 * util
    forward = l is not None and l.a_dev == dev.id
    in_oct = int(bps * uptime_s * (0.55 if forward else 1.0)) % 2 ** 64
    out_oct = int(bps * uptime_s * (1.0 if forward else 0.55)) % 2 ** 64
    errors = int(in_oct / 1e9 * (3 if st is not None and st.state == "warn" else 0.01))
    peer = ""
    if l:
        peer_id, peer_port = (l.b_dev, l.b_port) if l.a_dev == dev.id else (l.a_dev, l.a_port)
        peer = f"→ {net.devices[peer_id].name}:{peer_port} ({NC.CABLES[l.cable]['name']})"
    t_num, t_name = IF_TYPES.get(p.kind, (6, "ethernetCsmacd"))
    return {"ifIndex": idx, "ifDescr": p.name, "ifType": f"{t_name}({t_num})",
            "ifMtu": 9216 if p.mbps >= 10000 else 1500, "ifPhysAddress": mac_of(dev.id, p.name),
            "ifAdminStatus": "up(1)", "ifOperStatus": f"{STATUS[oper]}({oper})",
            "ifInErrors": errors, "ifHCInOctets": in_oct, "ifHCOutOctets": out_oct,
            "ifHighSpeed": int(speed), "ifAlias": peer, "kind": p.kind, "util": util, "speed_text": fmt_speed(speed)}


def _radio_rows(dev, tower):
    base = f"{ENTERPRISE}.2"
    r = dev.radio
    rows = [(f"{base}.1.0", "ZONEM-RAN::ranTechnology.0", f"STRING: {r.get('tech', 'LTE')}"),
            (f"{base}.2.0", "ZONEM-RAN::ranBandwidthMHz.0", f"Gauge32: {r.get('bw_mhz', 0):g}"),
            (f"{base}.3.0", "ZONEM-RAN::ranMimoLayers.0", f"INTEGER: {r.get('layers', 0)}"),
            (f"{base}.4.0", "ZONEM-RAN::ranMaxActiveUsers.0", f"INTEGER: {r.get('max_users', 0)}")]
    if tower is not None:
        rows += [(f"{base}.5.0", "ZONEM-RAN::ranFrequencyMHz.0", f"Gauge32: {tower.freq:g}"),
                 (f"{base}.6.0", "ZONEM-RAN::ranTxPowerW.0", f"STRING: {tower.power:g}"),
                 (f"{base}.7.0", "ZONEM-RAN::ranEirpDbm.0", f"STRING: {tower.eirp_dbm:.1f}"),
                 (f"{base}.8.0", "ZONEM-RAN::ranAntennaHeightM.0", f"STRING: {tower.height:g}"),
                 (f"{base}.9.0", "ZONEM-RAN::ranAzimuthDeg.0", f"INTEGER: {int(round(tower.azimuth))}"),
                 (f"{base}.10.0", "ZONEM-RAN::ranBeamwidthDeg.0", f"INTEGER: {int(round(tower.beamwidth))}"),
                 (f"{base}.11.0", "ZONEM-RAN::ranCellRadiusWeakM.0", f"Gauge32: {int(tower.r_weak * 1000)}")]
    return rows


def _poe_rows(net, dev):
    st = net.dev_state.get(dev.id, {})
    rows = [("1.3.6.1.2.1.105.1.3.1.1.2.1", "POWER-ETHERNET-MIB::pethMainPsePower.1",
             f"Gauge32: {dev.spec['poe_budget_w']:g} W"),
            ("1.3.6.1.2.1.105.1.3.1.1.4.1", "POWER-ETHERNET-MIB::pethMainPseConsumptionPower.1",
             f"Gauge32: {st.get('poe_used_w', 0.0):.0f} W")]
    for i, p in enumerate(dev.ports(), 1):
        if not p.pse:
            continue
        l = net.port_link(dev.id, p.name)
        delivering = False
        if l:
            peer_id = l.b_dev if l.a_dev == dev.id else l.a_dev
            peer = net.devices[peer_id]
            delivering = peer.power_mode == "poe" and net.dev_state.get(peer_id, {}).get("powered", False)
        state = "deliveringPower(3)" if delivering else "searching(2)"
        rows.append((f"1.3.6.1.2.1.105.1.1.1.6.1.{i}", f"POWER-ETHERNET-MIB::pethPsePortDetectionStatus.1.{i}",
                     f"INTEGER: {state}  [{p.name}, {NC.POE_NAMES[p.pse]}]"))
    return rows
