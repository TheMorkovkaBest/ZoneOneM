"""Создаёт examples/demo_project.json — готовую сеть вокруг центра Кишинёва.  python tools/make_demo.py

Импорт: «Файл → Импорт проекта» → «Заменить». Затем: «Авто-IP», режим PDU (Центр → CORE), «Тест нагрузки».
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import exporters                                    # noqa: E402
from network import Network                         # noqa: E402
from tower import DEFAULT_PRESETS, Tower            # noqa: E402

LAT0, LON0 = 47.0101, 28.8638
M_LAT, M_LON = 111320.0, 111320.0 * 0.6821


def at(east, north):
    return LAT0 + north / M_LAT, LON0 + east / M_LON


def build():
    types = dict(DEFAULT_PRESETS)
    macro = types["Макросота 800 МГц"]
    small = types["Низкая микросота"]
    sector = types["Секторная 1800 МГц (65°)"]
    towers = []
    for i, (name, tp, e, n, extra) in enumerate([
            ("Центр", dict(macro, freq=1800.0), 0, 0, {}),
            ("Восток 5G", dict(macro, freq=3500.0, power=30.0), 650, 120, {}),
            ("Дворик", small, -180, -90, {}),
            ("Сектор юг", sector, 80, -520, {"azimuth": 180.0})], 1):
        lat, lon = at(e, n)
        p = {k: tp[k] for k in ("freq", "power", "height", "tilt", "wall_loss", "azimuth", "beamwidth")}
        p.update(extra)
        towers.append(Tower(name, lat, lon, p["freq"], p["power"], p["height"], p["tilt"], p["wall_loss"],
                            tp["color"], azimuth=p["azimuth"], beamwidth=p["beamwidth"],
                            type_name=name if name in types else None, db_id=i))
    net = Network()
    net.sync_towers(towers)
    net.devices["T3"].power_mode = "poe"                      # малая сота питается по PoE от коммутатора
    sw = net.add_device("sw_l2_24poe", "SW-Центр", *at(60, 10))
    csr = net.add_device("rt_csr", "CSR-1", *at(120, -250))
    mw_a = net.add_device("mw_eband", "MW-Восток", *at(640, 110))
    mw_b = net.add_device("mw_eband", "MW-Центр", *at(130, -230), mount_h=25.0)
    agg = net.add_device("rt_agg", "AGG-1", *at(120, -900))
    core = net.add_device("core_5gc", "CORE", *at(120, -1700))

    def link(a, pa, b, pb, cable, length=None):
        l, err = net.connect(a if isinstance(a, str) else a.id, pa, b if isinstance(b, str) else b.id, pb, cable)
        assert l is not None, err
        l.length_m = length
    link("T1", "Te1", sw, "Te1/1/1", "f_10g_sr3", 90)
    link("T3", "Eth1", sw, "Gi1/0/1", "cat6a", 85)     # Cat6A до 100 м; длиннее — линк и PoE пропадут
    link("T4", "Te1", csr, "Te0/2", "f_10g_sr3", 300)
    link(sw, "Te1/1/2", csr, "Te0/1", "f_10g_lr", 280)
    link("T2", "Hu1", mw_a, "Eth1", "f_10g_sr3", 15)
    link(mw_a, "Air1", mw_b, "Air1", "mw_eband")
    link(mw_b, "Eth1", csr, "Te0/3", "f_10g_sr3", 40)
    link(csr, "Tw0/1", agg, "Te0/1", "f_10g_lr", 700)
    link(agg, "Hu0/1", core, "Hu1", "f_100g_lr4", 800)
    net.evaluate()
    return types, towers, net


def main():
    types, towers, net = build()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "demo_project.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(exporters.project_to_json(types, towers, None, [], net.to_dict()))
    print("Записано:", out)
    for lid, st in net.status.items():
        l = net.links[lid]
        print(f"  {l.id}: {net.devices[l.a_dev].name} ↔ {net.devices[l.b_dev].name}  {st.state}  {st.speed_mbps:g} Мбит/с  {st.reason}")


if __name__ == "__main__":
    main()
