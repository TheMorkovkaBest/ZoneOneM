"""Каталог сетевого оборудования (данные, без логики).

Параметры взяты из стандартов и обзоров 2025–2026 гг. (источники — в README):
  * медь:   Cat5e/Cat6 — 1G до 100 м, Cat6 10G только до ~55 м, Cat6A 10G до 100 м, Cat8 25/40G до 30 м;
  * PoE:    802.3af 15.4 Вт, at 30 Вт, bt Type 3 60 Вт, bt Type 4 90 Вт на порту;
  * оптика: SFP+ SR 300/400 м, LR 10 км, ER 40 км, ZR 80 км; QSFP28 SR4 100 м, FR/CWDM4 2 км,
            LR4 10 км, ER4 40 км, ZR4 80 км; QSFP-DD 400G SR8 100 м, DR4 500 м, FR4 2 км, LR4 10 км;
  * РРЛ:    6–42 ГГц до 10 Гбит/с и ~25–50 км, E-band 71–86 ГГц 10+ Гбит/с на 1–3 км,
            V-band 57–66 ГГц — совсем короткие линии (поглощение кислородом).
Значения РРЛ — инженерные оценки для моделирования, не паспорт конкретной модели.
"""

# --------------------------------------------------------------------------- #
#  Порты
# --------------------------------------------------------------------------- #
# Какие форм-факторы модулей принимает порт (обратная совместимость).
PORT_ACCEPTS = {
    "SFP": {"SFP"},
    "SFP+": {"SFP", "SFP+"},
    "SFP28": {"SFP", "SFP+", "SFP28"},
    "QSFP+": {"QSFP+"},
    "QSFP28": {"QSFP+", "QSFP28"},
    "QSFP-DD": {"QSFP+", "QSFP28", "QSFP-DD"},
}
OPTICAL_KINDS = tuple(PORT_ACCEPTS)
PORT_KINDS = ("RJ45",) + OPTICAL_KINDS + ("AIR",)

# PoE: тип -> (мощность на порту PSE, Вт; мощность, доступная устройству PD, Вт)
POE_TYPES = {"af": (15.4, 12.95), "at": (30.0, 25.5), "bt3": (60.0, 51.0), "bt4": (90.0, 71.3)}
POE_NAMES = {"af": "802.3af (15.4 Вт)", "at": "802.3at PoE+ (30 Вт)",
             "bt3": "802.3bt Type 3 (60 Вт)", "bt4": "802.3bt Type 4 (90 Вт)"}


def G(prefix, kind, mbps, n, pse=None, pd=None):
    """Группа однотипных портов: имена prefix1..prefixN."""
    return {"prefix": prefix, "kind": kind, "mbps": mbps, "n": n, "pse": pse, "pd": pd}


# --------------------------------------------------------------------------- #
#  Кабели и линии связи
# --------------------------------------------------------------------------- #
GROUP_NAMES = {"copper": "Витая пара (медь)", "fiber": "Оптоволокно + трансиверы",
               "dac": "DAC/AOC (короткие)", "radio": "Радиорелейные линии"}
GROUP_COLORS = {"copper": "#4cc9f0", "fiber": "#ffb703", "dac": "#b388ff", "radio": "#ff4d9d"}


def _copper(cid, name, tiers, cost_m, note):
    return {"id": cid, "name": name, "group": "copper", "connector": "RJ45", "tiers": tiers,
            "poe": True, "per_m": cost_m, "fixed": 4.0, "note": note}


def _optic(cid, name, form, mbps, max_m, fixed, per_m, note, medium):
    return {"id": cid, "name": name, "group": "fiber", "form": form, "mbps": mbps, "max_m": max_m,
            "fixed": fixed, "per_m": per_m, "note": note, "medium": medium}


def _dac(cid, name, form, mbps, max_m, fixed, note):
    return {"id": cid, "name": name, "group": "dac", "form": form, "mbps": mbps, "max_m": max_m,
            "fixed": fixed, "per_m": 0.0, "note": note, "medium": "медь/AOC"}


# tiers: [(скорость Мбит/с, макс. длина м)], от быстрого к медленному
_T_LOW = [(1000, 100), (100, 100), (10, 100)]
CABLES = {c["id"]: c for c in [
    _copper("cat5e", "Cat5e U/UTP", [(2500, 100)] + _T_LOW, 0.35, "1G до 100 м; 2.5G (802.3bz) до 100 м"),
    _copper("cat6", "Cat6 U/UTP", [(10000, 55), (5000, 100), (2500, 100)] + _T_LOW, 0.55,
            "1G до 100 м; 10G только до ~55 м"),
    _copper("cat6a", "Cat6A S/FTP", [(10000, 100), (5000, 100), (2500, 100)] + _T_LOW, 0.95,
            "10G до 100 м — стандарт для PoE++ и 10GBASE-T"),
    _copper("cat6a_out", "Cat6A уличный (UV/влагозащита)", [(10000, 100), (5000, 100), (2500, 100)] + _T_LOW, 1.6,
            "то же, для прокладки по мачте/фасаду"),
    _copper("cat8", "Cat8.1 S/FTP", [(40000, 30), (25000, 30), (10000, 100), (5000, 100), (2500, 100)] + _T_LOW, 1.9,
            "25/40GBASE-T только до 30 м (серверные/ЦОД)"),

    _optic("f_1g_sx", "1000BASE-SX MM (до 550 м)", "SFP", 1000, 550, 40, 0.4, "многомодовое OM3/OM4", "MM"),
    _optic("f_1g_lx", "1000BASE-LX SM (до 10 км)", "SFP", 1000, 10000, 70, 0.25, "одномодовое OS2", "SM"),
    _optic("f_1g_ex", "1000BASE-EX SM (до 40 км)", "SFP", 1000, 40000, 220, 0.25, "одномодовое OS2", "SM"),
    _optic("f_10g_sr3", "10GBASE-SR MM OM3 (до 300 м)", "SFP+", 10000, 300, 60, 0.5, "850 нм, 2 трансивера SFP+", "MM"),
    _optic("f_10g_sr4", "10GBASE-SR MM OM4 (до 400 м)", "SFP+", 10000, 400, 60, 0.7, "850 нм, 2 трансивера SFP+", "MM"),
    _optic("f_10g_lr", "10GBASE-LR SM (до 10 км)", "SFP+", 10000, 10000, 120, 0.25, "1310 нм", "SM"),
    _optic("f_10g_er", "10GBASE-ER SM (до 40 км)", "SFP+", 10000, 40000, 650, 0.25, "1550 нм", "SM"),
    _optic("f_10g_zr", "10GBASE-ZR SM (до 80 км)", "SFP+", 10000, 80000, 1200, 0.25, "1550 нм", "SM"),
    _optic("f_25g_sr", "25GBASE-SR MM OM4 (до 100 м)", "SFP28", 25000, 100, 120, 0.7, "850 нм", "MM"),
    _optic("f_25g_lr", "25GBASE-LR SM (до 10 км)", "SFP28", 25000, 10000, 350, 0.25, "1310 нм", "SM"),
    _optic("f_40g_sr4", "40GBASE-SR4 MM OM4 (до 150 м)", "QSFP+", 40000, 150, 160, 1.4, "MPO-12, 4 пары волокон", "MM"),
    _optic("f_40g_lr4", "40GBASE-LR4 SM (до 10 км)", "QSFP+", 40000, 10000, 700, 0.25, "CWDM", "SM"),
    _optic("f_100g_sr4", "100GBASE-SR4 MM OM4 (до 100 м)", "QSFP28", 100000, 100, 350, 1.4, "MPO-12", "MM"),
    _optic("f_100g_fr", "100G CWDM4/FR SM (до 2 км)", "QSFP28", 100000, 2000, 900, 0.25, "дуплексный LC", "SM"),
    _optic("f_100g_lr4", "100GBASE-LR4 SM (до 10 км)", "QSFP28", 100000, 10000, 1800, 0.25, "LWDM", "SM"),
    _optic("f_100g_er4", "100GBASE-ER4 SM (до 40 км)", "QSFP28", 100000, 40000, 5500, 0.25, "LWDM", "SM"),
    _optic("f_100g_zr4", "100G ZR4 SM (до 80 км)", "QSFP28", 100000, 80000, 9000, 0.25, "LWDM", "SM"),
    _optic("f_400g_sr8", "400GBASE-SR8 MM OM4 (до 100 м)", "QSFP-DD", 400000, 100, 1500, 2.5, "MPO-16", "MM"),
    _optic("f_400g_dr4", "400GBASE-DR4 SM (до 500 м)", "QSFP-DD", 400000, 500, 2500, 1.2, "MPO-12", "SM"),
    _optic("f_400g_fr4", "400GBASE-FR4 SM (до 2 км)", "QSFP-DD", 400000, 2000, 3500, 0.25, "CWDM", "SM"),
    _optic("f_400g_lr4", "400GBASE-LR4 SM (до 10 км)", "QSFP-DD", 400000, 10000, 6000, 0.25, "CWDM", "SM"),
    _optic("f_400g_zr", "400G ZR когерентный SM (до 80 км)", "QSFP-DD", 400000, 80000, 14000, 0.25, "когерентная оптика", "SM"),

    _dac("dac_10g", "DAC 10G SFP+ (пассивный, до 7 м)", "SFP+", 10000, 7, 25, "внутристоечный"),
    _dac("dac_25g", "DAC 25G SFP28 (пассивный, до 5 м)", "SFP28", 25000, 5, 45, "внутристоечный"),
    _dac("dac_40g", "DAC 40G QSFP+ (пассивный, до 5 м)", "QSFP+", 40000, 5, 60, "внутристоечный"),
    _dac("dac_100g", "DAC 100G QSFP28 (пассивный, до 5 м)", "QSFP28", 100000, 5, 90, "внутристоечный"),
    _dac("aoc_100g", "AOC 100G QSFP28 (активный оптический, до 100 м)", "QSFP28", 100000, 100, 300, "активный оптический"),
    _dac("dac_400g", "DAC 400G QSFP-DD (до 3 м)", "QSFP-DD", 400000, 3, 350, "внутристоечный"),
    _dac("aoc_400g", "AOC 400G QSFP-DD (до 30 м)", "QSFP-DD", 400000, 30, 1100, "активный оптический"),

    {"id": "mw_trad", "name": "РРЛ 6–42 ГГц (лицензируемая)", "group": "radio", "band": "trad",
     "fixed": 0.0, "per_m": 0.0, "note": "до 2.5 Гбит/с на хоп, десятки км, нужна прямая видимость"},
    {"id": "mw_eband", "name": "E-band 71–86 ГГц", "group": "radio", "band": "eband",
     "fixed": 0.0, "per_m": 0.0, "note": "до 10 Гбит/с, 1–3 км (до ~5 км со снижением доступности)"},
    {"id": "mw_vband", "name": "V-band 57–66 ГГц", "group": "radio", "band": "vband",
     "fixed": 0.0, "per_m": 0.0, "note": "до ~5 Гбит/с, до ~1 км (поглощение кислородом)"},
]}

# Радиорелейные диапазоны. ladder: (мин. уровень приёма дБм, ёмкость Мбит/с) по возрастанию.
RADIO_BANDS = {
    "trad": {"name": "РРЛ 6–42 ГГц", "freq_ghz": 23.0, "tx_dbm": 20.0, "gain_dbi": 38.0, "losses_db": 3.0,
             "rain_db_km": 1.2, "gas_db_km": 0.15, "max_km": 40.0,
             "ladder": [(-90, 100), (-84, 300), (-78, 600), (-72, 1000), (-66, 2500)]},
    "eband": {"name": "E-band 71–86 ГГц", "freq_ghz": 80.0, "tx_dbm": 15.0, "gain_dbi": 43.0, "losses_db": 3.0,
              "rain_db_km": 12.0, "gas_db_km": 0.5, "max_km": 5.0,
              "ladder": [(-70, 1000), (-64, 2500), (-58, 5000), (-54, 7500), (-50, 10000)]},
    "vband": {"name": "V-band 57–66 ГГц", "freq_ghz": 60.0, "tx_dbm": 10.0, "gain_dbi": 38.0, "losses_db": 3.0,
              "rain_db_km": 9.0, "gas_db_km": 15.0, "max_km": 1.5,
              "ladder": [(-70, 500), (-64, 1000), (-58, 2500), (-52, 5000)]},
}

# --------------------------------------------------------------------------- #
#  Устройства
# --------------------------------------------------------------------------- #
ROLE_NAMES = {"tower": "Базовые станции", "switch_l2": "Коммутаторы L2 (доступ)",
              "switch_l3": "Коммутаторы L3 (агрегация, ядро)", "router": "Маршрутизаторы",
              "firewall": "Межсетевые экраны", "radio": "Радиорелейные терминалы", "core": "Ядро сети / Интернет"}
L2_ROLES = ("switch_l2", "radio")                  # прозрачно передают кадры
L3_ROLES = ("switch_l3", "router", "firewall")     # маршрутизируют пакеты


def _model(mid, name, role, ports, power_w, cost, example, **kw):
    d = {"id": mid, "name": name, "role": role, "ports": ports, "power_w": power_w, "cost": cost,
         "example": example, "poe_budget_w": 0.0}
    d.update(kw)
    return d


MODELS = {m["id"]: m for m in [
    _model("bs_small", "Малая сота (PoE)", "tower",
           [G("Eth", "RJ45", 2500, 1, pd="at"), G("Te", "SFP+", 10000, 1)], 25, 1800, "класс small cell / pico",
           radio={"tech": "LTE", "bw_mhz": 20.0, "layers": 2, "max_users": 100}, mount_h=6.0),
    _model("bs_macro_lte", "Макро-БС LTE (eNodeB)", "tower",
           [G("Te", "SFP+", 10000, 2), G("Mgmt", "RJ45", 1000, 1)], 450, 14000, "класс macro eNodeB, 4T4R",
           radio={"tech": "LTE", "bw_mhz": 40.0, "layers": 4, "max_users": 400}, mount_h=30.0),
    _model("bs_macro_5g", "Макро-БС 5G NR (gNB)", "tower",
           [G("Hu", "SFP28", 25000, 2), G("Mgmt", "RJ45", 1000, 1)], 800, 32000, "класс macro gNB, 64T64R/32T32R",
           radio={"tech": "NR", "bw_mhz": 100.0, "layers": 4, "max_users": 600}, mount_h=30.0),

    _model("sw_l2_8poe", "Коммутатор L2 промышленный, 8×1G PoE+ + 2×SFP", "switch_l2",
           [G("Gi0/", "RJ45", 1000, 8, pse="at"), G("Sfp0/", "SFP", 1000, 2)], 25, 450,
           "класс 8-портовых PoE+ коммутаторов (240 Вт)", poe_budget_w=240.0, switching_gbps=20),
    _model("sw_l2_24poe", "Коммутатор L2 доступа, 24×1G PoE+ + 4×10G SFP+", "switch_l2",
           [G("Gi1/0/", "RJ45", 1000, 24, pse="at"), G("Te1/1/", "SFP+", 10000, 4)], 60, 1500,
           "класс Catalyst 9200/Aruba CX 6200 (370 Вт PoE)", poe_budget_w=370.0, switching_gbps=128),
    _model("sw_l2_48poe", "Коммутатор L2 доступа, 48×1G PoE+ + 4×10G SFP+", "switch_l2",
           [G("Gi1/0/", "RJ45", 1000, 48, pse="at"), G("Te1/1/", "SFP+", 10000, 4)], 90, 2800,
           "класс H3C S5560X-54C-PWR / Catalyst 9300 (740 Вт PoE)", poe_budget_w=740.0, switching_gbps=176),
    _model("sw_mgig_24bt", "Коммутатор Multi-Gig PoE++, 24×(до 10G) + 4×SFP+ + 4×SFP28", "switch_l2",
           [G("Mg1/0/", "RJ45", 10000, 24, pse="bt3"), G("Te1/1/", "SFP+", 10000, 4), G("Tw1/1/", "SFP28", 25000, 4)],
           120, 5200, "класс Multi-GE PoE++ (Ruijie S6120-24XMG / Aruba CX 6300M)", poe_budget_w=1440.0,
           switching_gbps=880),

    _model("sw_l3_agg10", "Коммутатор агрегации L3, 24×10G SFP+ + 2×100G QSFP28", "switch_l3",
           [G("Te1/0/", "SFP+", 10000, 24), G("Hu1/1/", "QSFP28", 100000, 2)], 250, 9000,
           "класс Catalyst 9500-24X / Huawei S5732", switching_gbps=880),
    _model("sw_l3_agg25", "Коммутатор агрегации L3, 48×SFP28 (1/10/25G) + 8×QSFP28", "switch_l3",
           [G("Tw1/0/", "SFP28", 25000, 48), G("Hu1/1/", "QSFP28", 100000, 8)], 450, 21000,
           "класс H3C S6550X-56HF / Huawei S6750-H48Y8C", switching_gbps=4000),
    _model("sw_l3_core100", "Коммутатор ядра L3, 32×100G QSFP28", "switch_l3",
           [G("Hu1/0/", "QSFP28", 100000, 32)], 650, 38000, "класс Nexus 9300 / Arista 7060 / QFX5200",
           switching_gbps=6400),
    _model("sw_l3_core400", "Коммутатор ядра L3, 32×400G QSFP-DD", "switch_l3",
           [G("Fh1/0/", "QSFP-DD", 400000, 32)], 1100, 95000, "класс Arista 7060X6 / Nexus 9364 / QFX5230",
           switching_gbps=25600),

    _model("rt_csr", "Cell Site Router (CSR): 8×SFP+ + 2×SFP28 + 4×RJ45", "router",
           [G("Te0/", "SFP+", 10000, 8), G("Tw0/", "SFP28", 25000, 2), G("Gi0/", "RJ45", 1000, 4)], 80, 4200,
           "класс Edgecore CSR300 / UfiSpace S9501 (синхронизация, -40…+65 °C)", switching_gbps=240),
    _model("rt_agg", "Агрегирующий маршрутизатор: 48×SFP+ + 4×QSFP28", "router",
           [G("Te0/", "SFP+", 10000, 48), G("Hu0/", "QSFP28", 100000, 4)], 300, 15000,
           "класс Edgecore AGR130 / ASR 920/9000", switching_gbps=1280),
    _model("rt_pe", "Маршрутизатор ядра (PE): 24×SFP28 + 6×QSFP28 + 2×QSFP-DD", "router",
           [G("Tw0/", "SFP28", 25000, 24), G("Hu0/", "QSFP28", 100000, 6), G("Fh0/", "QSFP-DD", 400000, 2)],
           700, 60000, "класс Edgecore AGR420 / Juniper MX / Cisco 8000", switching_gbps=4800),
    _model("fw_ngfw", "Межсетевой экран NGFW: 8×RJ45 + 4×SFP+", "firewall",
           [G("Gi0/", "RJ45", 1000, 8), G("Te0/", "SFP+", 10000, 4)], 150, 8000, "класс NGFW для периметра ядра",
           switching_gbps=40),

    _model("mw_trad", "РРЛ-терминал 6–42 ГГц", "radio",
           [G("Eth", "SFP+", 10000, 1), G("Gi", "RJ45", 1000, 1), G("Air", "AIR", 2500, 1)], 60, 12000,
           "класс Ericsson MINI-LINK / Huawei OptiX RTN", band="trad", mount_h=25.0),
    _model("mw_eband", "РРЛ-терминал E-band 71–86 ГГц", "radio",
           [G("Eth", "SFP+", 10000, 1), G("Air", "AIR", 10000, 1)], 55, 15000, "класс E-band 10G (2 ГГц канал)",
           band="eband", mount_h=20.0),
    _model("mw_vband", "РРЛ-терминал V-band 57–66 ГГц (PoE)", "radio",
           [G("Eth", "RJ45", 2500, 1, pd="bt3"), G("Sfp", "SFP+", 10000, 1), G("Air", "AIR", 5000, 1)], 20, 3500,
           "класс V-band small cell backhaul", band="vband", mount_h=8.0),

    _model("core_5gc", "Ядро сети (5GC/EPC) + выход в Интернет", "core",
           [G("Hu", "QSFP28", 100000, 2), G("Tw", "SFP28", 25000, 4), G("Gi", "RJ45", 1000, 2)], 900, 120000,
           "центр обработки данных оператора"),
]}

# Мощность, потребляемая устройством по PoE, Вт: берём power_w модели (если питание от PoE).


def default_model_for_tower(freq_mhz, height_m):
    """Подходящая по умолчанию модель БС для параметров вышки."""
    if height_m <= 8.0:
        return "bs_small"
    return "bs_macro_5g" if freq_mhz >= 3000 else "bs_macro_lte"


def model_ports(model_id):
    """[(имя порта, kind, max Мбит/с, pse, pd)] в порядке следования."""
    out = []
    for g in MODELS[model_id]["ports"]:
        for i in range(1, g["n"] + 1):
            out.append((f"{g['prefix']}{i}", g["kind"], g["mbps"], g["pse"], g["pd"]))
    return out
