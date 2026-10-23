"""База данных и каталог промышленных типов антенн базовых станций.

Содержит реальные серийные антенны ведущих мировых производителей:
- Huawei (AAU Massive MIMO 5G, Macro Panels, EasyMacro)
- Kathrein / Ericsson Antenna System (Multi-band, Tri-band, Small Cell)
- Ericsson (AIR Massive MIMO 64T64R, Street Macro, Radio Dot)
- CommScope / Andrew (Ultra-Wideband, High-Band Narrow Beam, Multiband)
- Tongyu Communication (Multi-band Cross-pol)
- Comet / Diamond / Panorama (Omni 360°, Low-profile IoT)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


@dataclass
class AntennaType:
    id: str
    manufacturer: str
    model: str
    category: str              # 5G Massive MIMO, 4G/5G Macro Panel, Small Cell / Micro, Omni 360°, IoT/LPWAN
    freq_mhz: float            # Номинальная рабочая частота (МГц)
    freq_range: str            # Рабочий диапазон (текст)
    gain_dbi: float            # Коэффициент усиления (дБи)
    tx_power_w: float          # Номинальная подводимая мощность передатчика (Вт)
    beamwidth_h: float         # Ширина ДН в горизонтальной плоскости по уровню -3 дБ (градусы)
    beamwidth_v: float         # Ширина ДН в вертикальной плоскости (градусы)
    tilt_range: str            # Диапазон наклона луча (электрический RET / механический)
    wall_loss_db: float        # Типовое ослабление в стенах (дБ)
    polarization: str          # Поляризация (Cross-pol ±45°, Linear Vertical, MIMO)
    ports: str                 # Порты / конфигурация приёмопередатчиков
    dimensions_mm: str         # Габариты (В x Ш x Г, мм)
    weight_kg: float           # Масса (кг)
    suggested_mount: str       # Рекомендуемый тип монтажа
    description: str           # Описание и назначение

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.model} ({self.category})"


# Встроенный каталог серийных антенн (38 моделей мировых производителей)
BUILTIN_ANTENNAS: list[AntennaType] = [
    # --- Huawei 5G Massive MIMO & Macro ---
    AntennaType(
        id="huawei_aau5613",
        manufacturer="Huawei",
        model="AAU5613 64T64R",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="3400 - 3800 MHz (n78)",
        gain_dbi=24.5,
        tx_power_w=200.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Электронный eTilt: -2° … +13°",
        wall_loss_db=22.0,
        polarization="64T64R Massive MIMO (±45°)",
        ports="Встроенный приёмопередатчик 64T64R (CPRI/eCPRI)",
        dimensions_mm="795 x 395 x 190",
        weight_kg=28.5,
        suggested_mount="Каркас вышки, мачта, кровля",
        description="Флагманская активная антенная система 5G NR Massive MIMO для ультра-плотной городской застройки. Поддержка dynamic beamforming и 16 пространственных потоков.",
    ),
    AntennaType(
        id="huawei_aau5835",
        manufacturer="Huawei",
        model="AAU5835 Dual-Band",
        category="5G Massive MIMO",
        freq_mhz=2600.0,
        freq_range="1800 - 2100 / 2600 MHz",
        gain_dbi=22.0,
        tx_power_w=320.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="eTilt: 0° … +12°",
        wall_loss_db=20.0,
        polarization="32T32R Dual-Band Cross-pol",
        ports="eCPRI 2x 25GE",
        dimensions_mm="890 x 420 x 200",
        weight_kg=32.0,
        suggested_mount="Каркас вышки, опора",
        description="Двухдиапазонный активный антенный блок FDD Massive MIMO для совместного развёртывания LTE и 5G NR с максимальной ёмкостью.",
    ),
    AntennaType(
        id="huawei_aau5339w",
        manufacturer="Huawei",
        model="AAU5339w 32T32R",
        category="5G Massive MIMO",
        freq_mhz=2100.0,
        freq_range="1920 - 2170 MHz (B1/n1)",
        gain_dbi=22.5,
        tx_power_w=240.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="eTilt: 0° … +10°",
        wall_loss_db=20.0,
        polarization="32T32R Cross-pol",
        ports="eCPRI 2x 25GE",
        dimensions_mm="1400 x 395 x 190",
        weight_kg=36.0,
        suggested_mount="Каркас решётчатой башни, мачта",
        description="Активная антенная система FDD Massive MIMO суб-3ГГц для многократного увеличения ёмкости макро-сети LTE и 5G.",
    ),
    AntennaType(
        id="huawei_blade_aau_pro",
        manufacturer="Huawei",
        model="Blade AAU Pro",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="Sub-3GHz Passive + 3500 MHz 64T64R Active",
        gain_dbi=25.0,
        tx_power_w=320.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Active eTilt -2°…+13°, Passive RET 2°…12°",
        wall_loss_db=22.0,
        polarization="Active 64T64R + Passive Multiband",
        ports="eCPRI 2x 25GE + 10x 4.3-10 Female",
        dimensions_mm="2000 x 420 x 240",
        weight_kg=48.0,
        suggested_mount="Каркас вышки, усиленная мачта",
        description="Гибридная интегрированная антенна: верхняя секция — 5G Massive MIMO 64T64R, нижняя — полнодиапазонная пассивная антенна (Sub-3G). Экономит место на опоре.",
    ),
    AntennaType(
        id="huawei_atr4518r8",
        manufacturer="Huawei",
        model="ATR4518R8 Octa-Band",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="690 - 960 / 1710 - 2690 MHz",
        gain_dbi=18.0,
        tx_power_w=80.0,
        beamwidth_h=65.0,
        beamwidth_v=7.5,
        tilt_range="RET 2° … 12° независимый на каждый диапазон",
        wall_loss_db=18.0,
        polarization="Dual Cross-pol ±45° (16 портов)",
        ports="16x 4.3-10 Female",
        dimensions_mm="1499 x 380 x 149",
        weight_kg=22.5,
        suggested_mount="Каркас вышки, трубостойка на здании",
        description="Восьмидиапазонная пассивная панельная антенна для базовых станций 2G/3G/4G/5G с поддержкой 4T4R MIMO.",
    ),
    AntennaType(
        id="huawei_a264521r0",
        manufacturer="Huawei",
        model="A264521R0V06 Quad-Band",
        category="4G/5G Macro Panel",
        freq_mhz=2600.0,
        freq_range="1710 - 2690 MHz (4x High-Band)",
        gain_dbi=19.5,
        tx_power_w=120.0,
        beamwidth_h=65.0,
        beamwidth_v=5.5,
        tilt_range="RET 2° … 12°",
        wall_loss_db=20.0,
        polarization="4x Cross-pol ±45° (8 портов)",
        ports="8x 4.3-10 Female",
        dimensions_mm="2090 x 369 x 149",
        weight_kg=25.0,
        suggested_mount="Каркас вышки, мачта",
        description="Высокоэффективная четырёхдиапазонная панельная антенна 4T4R для диапазонов 1800/2100/2600 МГц с повышенным усилением.",
    ),
    AntennaType(
        id="huawei_easymacro",
        manufacturer="Huawei",
        model="EasyMacro 3.0",
        category="Small Cell / Micro",
        freq_mhz=2600.0,
        freq_range="1800 - 2600 MHz",
        gain_dbi=12.5,
        tx_power_w=20.0,
        beamwidth_h=65.0,
        beamwidth_v=15.0,
        tilt_range="Фиксированный / наклон кронштейном 0° … 10°",
        wall_loss_db=16.0,
        polarization="Dual-slant ±45°",
        ports="4x 4.3-10",
        dimensions_mm="450 x 220 x 130",
        weight_kg=8.5,
        suggested_mount="Столб уличного освещения, стена здания",
        description="Компактная цилиндрическая макро-антенна для устранения 'белых пятен' на оживлённых улицах и площадях.",
    ),
    AntennaType(
        id="huawei_bookrru",
        manufacturer="Huawei",
        model="Book RRU Sector",
        category="Small Cell / Micro",
        freq_mhz=1800.0,
        freq_range="1800 - 2100 MHz",
        gain_dbi=10.0,
        tx_power_w=10.0,
        beamwidth_h=90.0,
        beamwidth_v=25.0,
        tilt_range="Фиксированный настенный кронштейн",
        wall_loss_db=14.0,
        polarization="Dual Polarized ±45°",
        ports="PoE / DC + Оптика eCPRI",
        dimensions_mm="300 x 200 x 100",
        weight_kg=4.5,
        suggested_mount="Фасад здания, рекламный щит, фонарный столб",
        description="Ультракомпактный микро-узел с габаритами книги для незаметной установки на исторических улицах и остановках транспорта.",
    ),
    AntennaType(
        id="huawei_lampsite",
        manufacturer="Huawei",
        model="LampSite pRRU5951",
        category="Small Cell / Micro",
        freq_mhz=2100.0,
        freq_range="1800 - 3500 MHz Multiband",
        gain_dbi=4.5,
        tx_power_w=0.5,
        beamwidth_h=360.0,
        beamwidth_v=70.0,
        tilt_range="Потолочный монтаж",
        wall_loss_db=8.0,
        polarization="MIMO 4T4R Omnidirectional",
        ports="PoE++ Ethernet / Питание по витой паре",
        dimensions_mm="200 x 200 x 45",
        weight_kg=1.3,
        suggested_mount="Потолок ТРЦ, бизнес-центра, вокзала",
        description="Пикосота распределённой антенной системы (DAS/LampSite) для плотного indoor-покрытия торговых центров и аэропортов.",
    ),

    # --- Ericsson 5G Massive MIMO & Small Cell ---
    AntennaType(
        id="ericsson_air6449",
        manufacturer="Ericsson",
        model="AIR 6449 B41/n78",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="3400 - 3800 MHz (n78)",
        gain_dbi=25.0,
        tx_power_w=320.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Advanced Digital Beamforming: -15° … +15°",
        wall_loss_db=22.0,
        polarization="64T64R Dual-polarized",
        ports="4x 25GE eCPRI SFP28",
        dimensions_mm="841 x 431 x 205",
        weight_kg=34.0,
        suggested_mount="Каркас решётчатой башни, крышная опора",
        description="Флагманский активный антенный блок 5G Massive MIMO 64T64R компании Ericsson мощностью 320 Вт для рекордных скоростей в C-диапазоне.",
    ),
    AntennaType(
        id="ericsson_air6419",
        manufacturer="Ericsson",
        model="AIR 6419 Mid-Band",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="3400 - 3800 MHz (n78)",
        gain_dbi=25.0,
        tx_power_w=200.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Digital Tilt -15° … +15°",
        wall_loss_db=22.0,
        polarization="64T64R Dual-polarized",
        ports="eCPRI 2x 25GE",
        dimensions_mm="790 x 410 x 170",
        weight_kg=19.2,
        suggested_mount="Каркас вышки, мачта, трубостойка на кровле",
        description="Сверхлёгкий (всего 19.2 кг) блок 64T64R Massive MIMO с низким ветровым сопротивлением для быстрой модернизации существующих площадок.",
    ),
    AntennaType(
        id="ericsson_air3246",
        manufacturer="Ericsson",
        model="AIR 3246 FDD MIMO",
        category="5G Massive MIMO",
        freq_mhz=2100.0,
        freq_range="1920 - 2170 MHz (B1)",
        gain_dbi=21.5,
        tx_power_w=200.0,
        beamwidth_h=65.0,
        beamwidth_v=8.0,
        tilt_range="Digital Tilt -2° … +12°",
        wall_loss_db=20.0,
        polarization="32T32R Cross-pol",
        ports="eCPRI 2x 10/25G",
        dimensions_mm="1400 x 380 x 210",
        weight_kg=41.0,
        suggested_mount="Каркас вышки, мачта",
        description="Первая в отрасли коммерческая антенна FDD Massive MIMO 32T32R, утраивающая ёмкость сети LTE/5G в полосе 2.1 ГГц.",
    ),
    AntennaType(
        id="ericsson_air1641",
        manufacturer="Ericsson",
        model="AIR 1641 16T16R",
        category="5G Massive MIMO",
        freq_mhz=2600.0,
        freq_range="2496 - 2690 MHz (B41/n41)",
        gain_dbi=21.0,
        tx_power_w=160.0,
        beamwidth_h=65.0,
        beamwidth_v=8.0,
        tilt_range="Digital Tilt -2° … +12°",
        wall_loss_db=20.0,
        polarization="16T16R Cross-pol",
        ports="eCPRI 2x 25GE",
        dimensions_mm="750 x 370 x 180",
        weight_kg=17.5,
        suggested_mount="Каркас вышки, трубостойка",
        description="Облегчённая 16T16R антенна для высокоскоростного расширения ёмкости пригородных и шоссейных секторов.",
    ),
    AntennaType(
        id="ericsson_streetmacro_6701",
        manufacturer="Ericsson",
        model="Street Macro 6701",
        category="Small Cell / Micro",
        freq_mhz=3500.0,
        freq_range="3300 - 3800 MHz",
        gain_dbi=14.0,
        tx_power_w=10.0,
        beamwidth_h=90.0,
        beamwidth_v=12.0,
        tilt_range="Механический наклон кронштейна",
        wall_loss_db=15.0,
        polarization="Dual Polarized 4T4R",
        ports="Питание DC / PoE++, SFP+ оптический",
        dimensions_mm="380 x 240 x 120",
        weight_kg=7.5,
        suggested_mount="Фасад здания, столб",
        description="Компактная базовая станция уличного монтажа с интегрированной направленной антенной 90°.",
    ),
    AntennaType(
        id="ericsson_radio_dot",
        manufacturer="Ericsson",
        model="Radio Dot 4453",
        category="Small Cell / Micro",
        freq_mhz=2600.0,
        freq_range="1800 - 3500 MHz",
        gain_dbi=4.0,
        tx_power_w=0.25,
        beamwidth_h=360.0,
        beamwidth_v=80.0,
        tilt_range="Потолочный монтаж",
        wall_loss_db=6.0,
        polarization="MIMO 4T4R",
        ports="LAN RJ-45 (PoE / Dot Controller)",
        dimensions_mm="100 x 100 x 30",
        weight_kg=0.3,
        suggested_mount="Потолок офисов, переговорных, клиник",
        description="Миниатюрный внутренний диск связи Radio Dot для создания премиум-покрытия внутри зданий любого масштаба.",
    ),

    # --- Kathrein / Ericsson Antenna System ---
    AntennaType(
        id="kathrein_80010892",
        manufacturer="Kathrein",
        model="80010892 Hexa-Band",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="790 - 960 / 1710 - 2690 MHz",
        gain_dbi=18.2,
        tx_power_w=80.0,
        beamwidth_h=65.0,
        beamwidth_v=6.8,
        tilt_range="RCU / RET 0.5° … 10°",
        wall_loss_db=18.0,
        polarization="Dual ±45° (12 портов)",
        ports="12x 7/16 DIN или 4.3-10",
        dimensions_mm="1548 x 374 x 160",
        weight_kg=24.0,
        suggested_mount="Каркас башни, мачта",
        description="Классическая шестидиапазонная секторная антенна с превосходным подавлением заднего лепестка (Front-to-Back > 30 дБ).",
    ),
    AntennaType(
        id="kathrein_80010622",
        manufacturer="Kathrein",
        model="80010622 Tri-Band",
        category="4G/5G Macro Panel",
        freq_mhz=800.0,
        freq_range="790 - 960 / 1710 - 2170 MHz",
        gain_dbi=17.0,
        tx_power_w=60.0,
        beamwidth_h=65.0,
        beamwidth_v=8.5,
        tilt_range="Электрический RET 0° … 10°",
        wall_loss_db=15.0,
        polarization="Dual ±45°",
        ports="6x 7/16 DIN",
        dimensions_mm="1936 x 262 x 116",
        weight_kg=19.5,
        suggested_mount="Каркас вышки, мачта на растяжках",
        description="Трёхдиапазонная антенна для макро-покрытия пригородов и трасс на частотах LTE 800 / GSM 900 / UMTS 2100.",
    ),
    AntennaType(
        id="kathrein_80010764",
        manufacturer="Kathrein",
        model="80010764 High-Gain Dual",
        category="4G/5G Macro Panel",
        freq_mhz=2600.0,
        freq_range="1710 - 2690 MHz",
        gain_dbi=19.2,
        tx_power_w=100.0,
        beamwidth_h=65.0,
        beamwidth_v=4.5,
        tilt_range="RET 0° … 8°",
        wall_loss_db=20.0,
        polarization="Dual ±45°",
        ports="4x 7/16 DIN",
        dimensions_mm="2600 x 262 x 116",
        weight_kg=23.0,
        suggested_mount="Каркас решётчатой башни, мачта",
        description="Длиннофокусная двухметровая антенна с узким вертикальным лучом 4.5° и усилением 19.2 дБи для дальнобойных позиций.",
    ),
    AntennaType(
        id="kathrein_80010991",
        manufacturer="Kathrein",
        model="80010991 Deca-Band",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="698 - 960 / 1695 - 2690 / 3400 - 3800 MHz",
        gain_dbi=18.5,
        tx_power_w=120.0,
        beamwidth_h=65.0,
        beamwidth_v=6.5,
        tilt_range="Независимый RET на каждый диапазон",
        wall_loss_db=19.0,
        polarization="Cross-pol ±45° (20 портов)",
        ports="20x 4.3-10 Female",
        dimensions_mm="2100 x 430 x 170",
        weight_kg=33.5,
        suggested_mount="Каркас вышки, прочная трубостойка",
        description="Десятидиапазонная антенна, включающая C-band (3.5 ГГц), позволяющая объединить все существующие поколения связи на одной панели.",
    ),
    AntennaType(
        id="kathrein_84010515",
        manufacturer="Kathrein",
        model="84010515 Street Omni",
        category="Omni 360°",
        freq_mhz=2100.0,
        freq_range="1710 - 2690 MHz",
        gain_dbi=8.5,
        tx_power_w=25.0,
        beamwidth_h=360.0,
        beamwidth_v=14.0,
        tilt_range="Фиксированный круговой 0°",
        wall_loss_db=14.0,
        polarization="Linear Vertical",
        ports="2x N-Female",
        dimensions_mm="650 x 85 x 85 (цилиндр)",
        weight_kg=3.2,
        suggested_mount="Столб, трубостойка на крыше павильона",
        description="Круговая ненаправленная антенна для уличного микросотового покрытия и пешеходных зон.",
    ),
    AntennaType(
        id="kathrein_742215",
        manufacturer="Kathrein",
        model="742215 Low-Band Omni",
        category="Omni 360°",
        freq_mhz=900.0,
        freq_range="790 - 960 MHz",
        gain_dbi=5.15,
        tx_power_w=50.0,
        beamwidth_h=360.0,
        beamwidth_v=36.0,
        tilt_range="0° горизонтальный",
        wall_loss_db=12.0,
        polarization="Linear Vertical",
        ports="1x 7/16 DIN",
        dimensions_mm="1050 x 50 x 50 (штырь)",
        weight_kg=2.5,
        suggested_mount="Мачта на крыше, вершина вышки",
        description="Ненаправленная коллинеарная антенна низких частот (LTE 800 / GSM 900) для диспетчерской связи и охвата территории 360°.",
    ),

    # --- CommScope / Andrew ---
    AntennaType(
        id="commscope_rrzzt4s4",
        manufacturer="CommScope",
        model="RRZZT4S4-65B-R6",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="698 - 960 / 1695 - 2690 MHz",
        gain_dbi=18.5,
        tx_power_w=120.0,
        beamwidth_h=65.0,
        beamwidth_v=6.5,
        tilt_range="Встроенный SmartRET 2° … 14°",
        wall_loss_db=18.0,
        polarization="Dual ±45° (12 портов)",
        ports="12x 4.3-10 Female",
        dimensions_mm="1828 x 498 x 197",
        weight_kg=31.2,
        suggested_mount="Каркас решётчатой башни, мачта",
        description="Высокопроизводительная 12-портовая широкополосная антенна с высокой ветроустойчивостью и отличной изоляцией портов.",
    ),
    AntennaType(
        id="commscope_2hh38a",
        manufacturer="CommScope",
        model="Argus 2HH-38A-R4",
        category="4G/5G Macro Panel",
        freq_mhz=2600.0,
        freq_range="1695 - 2690 MHz",
        gain_dbi=20.5,
        tx_power_w=100.0,
        beamwidth_h=38.0,
        beamwidth_v=6.0,
        tilt_range="RET 0° … 10°",
        wall_loss_db=22.0,
        polarization="Dual ±45°",
        ports="4x 7/16 DIN",
        dimensions_mm="1390 x 300 x 150",
        weight_kg=16.0,
        suggested_mount="Каркас вышки, кровля",
        description="Узколучевая секторная антенна 38° с повышенным усилением (20.5 дБи) для многосекторных площадок стадионов и мостов.",
    ),
    AntennaType(
        id="commscope_ffvv_65b_r2",
        manufacturer="CommScope",
        model="FFVV-65B-R2 16-Port",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="617 - 894 / 1695 - 2690 MHz",
        gain_dbi=18.3,
        tx_power_w=150.0,
        beamwidth_h=65.0,
        beamwidth_v=6.2,
        tilt_range="SmartRET 2° … 12°",
        wall_loss_db=18.0,
        polarization="Dual slant ±45° (16 портов)",
        ports="16x 4.3-10 Female",
        dimensions_mm="1830 x 498 x 197",
        weight_kg=35.0,
        suggested_mount="Каркас вышки, мачта",
        description="16-портовая многодиапазонная антенна для совместного размещения сетей 4G 4x4 MIMO и низкочастотного 5G NR (600/700/800 МГц).",
    ),
    AntennaType(
        id="commscope_nnvv_65b_r4",
        manufacturer="CommScope",
        model="NNVV-65B-R4 8-Port",
        category="4G/5G Macro Panel",
        freq_mhz=2100.0,
        freq_range="1695 - 2690 MHz Quad High-Band",
        gain_dbi=18.0,
        tx_power_w=100.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="RET 2° … 12°",
        wall_loss_db=19.0,
        polarization="Dual slant ±45°",
        ports="8x 4.3-10 Female",
        dimensions_mm="1500 x 301 x 181",
        weight_kg=18.2,
        suggested_mount="Каркас башни, трубостойка",
        description="Компактная 8-портовая антенна средней длины для городских секторов с высокой плотностью абонентов.",
    ),
    AntennaType(
        id="commscope_cellmax",
        manufacturer="CommScope",
        model="Cell-Max Indoor Dome",
        category="Omni 360°",
        freq_mhz=1800.0,
        freq_range="698 - 2700 MHz",
        gain_dbi=5.2,
        tx_power_w=10.0,
        beamwidth_h=360.0,
        beamwidth_v=60.0,
        tilt_range="Потолочный монтаж",
        wall_loss_db=6.0,
        polarization="Linear Vertical",
        ports="1x N-Female / 4.3-10",
        dimensions_mm="186 x 186 x 85 (купол)",
        weight_kg=0.45,
        suggested_mount="Подвесной потолок внутри помещений",
        description="Внутренняя потолочная широкополосная антенна купольного типа для распределённых антенных сетей DAS в зданиях.",
    ),

    # --- Nokia AirScale ---
    AntennaType(
        id="nokia_airscale_aehc",
        manufacturer="Nokia",
        model="AirScale MAA AEHC 64T64R",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="3400 - 3800 MHz (n78)",
        gain_dbi=25.0,
        tx_power_w=200.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Digital Beamforming Tilt -15° … +15°",
        wall_loss_db=22.0,
        polarization="64T64R Dual-polarized",
        ports="eCPRI 2x 25GE SFP28",
        dimensions_mm="820 x 410 x 180",
        weight_kg=29.0,
        suggested_mount="Каркас башни, мачта, кровля",
        description="Активная антенная система Nokia AirScale Massive MIMO 64T64R мощностью 200 Вт для максимальной ёмкости 5G сетей.",
    ),
    AntennaType(
        id="nokia_airscale_aeua",
        manufacturer="Nokia",
        model="AirScale MAA AEUA 32T32R",
        category="5G Massive MIMO",
        freq_mhz=2600.0,
        freq_range="2496 - 2690 MHz (n41)",
        gain_dbi=23.5,
        tx_power_w=240.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="Digital Tilt -10° … +10°",
        wall_loss_db=21.0,
        polarization="32T32R Dual-polarized",
        ports="eCPRI 2x 25GE",
        dimensions_mm="910 x 410 x 195",
        weight_kg=31.0,
        suggested_mount="Каркас вышки, мачта",
        description="Сбалансированная 32T32R активная антенна для частот 2.6 ГГц с поддержкой гибкого формирования нескольких лучей.",
    ),
    AntennaType(
        id="nokia_airscale_dual4t4r",
        manufacturer="Nokia",
        model="AirScale Dual 4T4R Panel",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="1710 - 2170 MHz Dual-band",
        gain_dbi=18.2,
        tx_power_w=160.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="RET 0° … 10°",
        wall_loss_db=18.0,
        polarization="Cross-pol ±45° (8 портов)",
        ports="8x 4.3-10 Female",
        dimensions_mm="1500 x 320 x 150",
        weight_kg=19.0,
        suggested_mount="Каркас вышки, опора",
        description="Классическая двухдиапазонная макро-панель Nokia AirScale с высокой линейностью характеристик для сетей 4G/5G DSS.",
    ),
    AntennaType(
        id="nokia_flexi_zone",
        manufacturer="Nokia",
        model="Flexi Zone Micro Outdoor",
        category="Small Cell / Micro",
        freq_mhz=2600.0,
        freq_range="2500 - 2690 MHz",
        gain_dbi=11.0,
        tx_power_w=5.0,
        beamwidth_h=65.0,
        beamwidth_v=20.0,
        tilt_range="Регулируемый настенный кронштейн",
        wall_loss_db=14.0,
        polarization="Cross-pol ±45° 2T2R",
        ports="PoE+ Ethernet / Оптика SFP",
        dimensions_mm="280 x 220 x 95",
        weight_kg=4.2,
        suggested_mount="Фонарный столб, стена здания",
        description="Уличная микросота Nokia Flexi Zone для оперативного устранения дефицита ёмкости на перекрёстках и площадях.",
    ),

    # --- Tongyu Communication ---
    AntennaType(
        id="tongyu_ty_ad1820",
        manufacturer="Tongyu",
        model="TY-AD1820-65D",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="1710 - 2170 MHz",
        gain_dbi=18.0,
        tx_power_w=40.0,
        beamwidth_h=65.0,
        beamwidth_v=7.0,
        tilt_range="Электрический 0° … 10°",
        wall_loss_db=18.0,
        polarization="Cross-pol ±45°",
        ports="4x 7/16 DIN",
        dimensions_mm="1350 x 165 x 100",
        weight_kg=11.5,
        suggested_mount="Каркас вышки, трубостойка",
        description="Надёжная и экономичная макро-антенна для диапазонов DCS 1800 и UMTS 2100.",
    ),
    AntennaType(
        id="tongyu_ty_5g_64t64r",
        manufacturer="Tongyu",
        model="TY-5G-AAU-64T64R",
        category="5G Massive MIMO",
        freq_mhz=3500.0,
        freq_range="3400 - 3800 MHz (n78)",
        gain_dbi=24.5,
        tx_power_w=200.0,
        beamwidth_h=65.0,
        beamwidth_v=6.0,
        tilt_range="Цифровой Tilt -12° … +12°",
        wall_loss_db=22.0,
        polarization="64T64R Cross-pol",
        ports="2x 25GE eCPRI",
        dimensions_mm="810 x 400 x 185",
        weight_kg=27.5,
        suggested_mount="Каркас вышки, мачта",
        description="Активная антенная система 64T64R с высокой энергоэффективностью и сниженной парусностью.",
    ),
    AntennaType(
        id="tongyu_tys_081819",
        manufacturer="Tongyu",
        model="TYS-081819-65D Tri-Band",
        category="4G/5G Macro Panel",
        freq_mhz=1800.0,
        freq_range="790 - 960 / 1710 - 2690 MHz",
        gain_dbi=18.2,
        tx_power_w=80.0,
        beamwidth_h=65.0,
        beamwidth_v=6.8,
        tilt_range="RET 0° … 10°",
        wall_loss_db=18.0,
        polarization="Cross-pol ±45° (6 портов)",
        ports="6x 4.3-10 Female",
        dimensions_mm="1980 x 280 x 140",
        weight_kg=21.0,
        suggested_mount="Каркас вышки, мачта на растяжках",
        description="Трёхдиапазонная базовая антенна с отличным КСВн и повышенной грозозащитой.",
    ),

    # --- MikroTik / Ubiquiti (WISP & Fixed Wireless Access) ---
    AntennaType(
        id="mikrotik_mantbox_15s",
        manufacturer="MikroTik",
        model="mANTBox 15s Sector",
        category="WISP / Фиксированный БШПД",
        freq_mhz=5500.0,
        freq_range="5150 - 5875 MHz (5 GHz)",
        gain_dbi=15.0,
        tx_power_w=1.0,
        beamwidth_h=120.0,
        beamwidth_v=10.0,
        tilt_range="Механический наклон кронштейном",
        wall_loss_db=25.0,
        polarization="Dual Linear (V / H)",
        ports="1x Gigabit Ethernet (PoE-in)",
        dimensions_mm="140 x 348 x 82",
        weight_kg=1.35,
        suggested_mount="Мачта, трубостойка, стена здания",
        description="Секторная точка доступа 5 ГГц с шириной луча 120° со встроенным маршрутизатором RouterOS для раздачи беспроводного интернета в посёлках.",
    ),
    AntennaType(
        id="mikrotik_mant_19s",
        manufacturer="MikroTik",
        model="mANT 19s Dual-Pol",
        category="WISP / Фиксированный БШПД",
        freq_mhz=5500.0,
        freq_range="5170 - 5825 MHz",
        gain_dbi=19.0,
        tx_power_w=2.0,
        beamwidth_h=120.0,
        beamwidth_v=5.0,
        tilt_range="Регулируемый узел крепления quickMOUNT",
        wall_loss_db=26.0,
        polarization="Dual Linear (V / H)",
        ports="2x RP-SMA Female",
        dimensions_mm="152 x 598 x 78",
        weight_kg=2.8,
        suggested_mount="Мачта, каркас вышки",
        description="Усиленная секторная антенна 5 ГГц 120° с усилением 19 дБи для базовых станций операторов широкополосного беспроводного доступа.",
    ),
    AntennaType(
        id="ubiquiti_airmax_ac_sector",
        manufacturer="Ubiquiti",
        model="airMAX AC Sector AP-5AC-90-HD",
        category="WISP / Фиксированный БШПД",
        freq_mhz=5500.0,
        freq_range="5150 - 5850 MHz",
        gain_dbi=22.0,
        tx_power_w=1.5,
        beamwidth_h=60.0,
        beamwidth_v=4.0,
        tilt_range="Механический наклон -10° … +10°",
        wall_loss_db=28.0,
        polarization="Dual-slant 45°",
        ports="2x RP-SMA",
        dimensions_mm="722 x 372 x 125",
        weight_kg=5.2,
        suggested_mount="Каркас вышки, мачта",
        description="Высокоплотная секторная антенна airMAX AC с узким лучом 60° и превосходной помехоустойчивостью для дальнобойных каналов связи.",
    ),
    AntennaType(
        id="ubiquiti_prismstation_5ac",
        manufacturer="Ubiquiti",
        model="PrismStation 5AC Horn",
        category="WISP / Фиксированный БШПД",
        freq_mhz=5500.0,
        freq_range="5150 - 5875 MHz",
        gain_dbi=14.0,
        tx_power_w=1.0,
        beamwidth_h=45.0,
        beamwidth_v=45.0,
        tilt_range="Шарнирный кронштейн 360°",
        wall_loss_db=24.0,
        polarization="Dual Linear",
        ports="Gigabit PoE RJ-45",
        dimensions_mm="174 x 174 x 184 (рупор)",
        weight_kg=1.4,
        suggested_mount="Трубостойка, мачта",
        description="Рупорная антенна симметричной формы 45° с нулевыми боковыми лепестками, идеальна для кластерной установки на мачтах в шумном радиоэфире.",
    ),

    # --- Круговые Omni и IoT / LPWAN ---
    AntennaType(
        id="diamond_f22_omni",
        manufacturer="Diamond / Comet",
        model="F22 Station Omni",
        category="Omni 360°",
        freq_mhz=800.0,
        freq_range="790 - 900 MHz (LTE 800 / GSM 900 / NB-IoT)",
        gain_dbi=6.5,
        tx_power_w=50.0,
        beamwidth_h=360.0,
        beamwidth_v=28.0,
        tilt_range="Фиксированный круговой 0°",
        wall_loss_db=12.0,
        polarization="Linear Vertical",
        ports="1x N-Female",
        dimensions_mm="1200 x 38 x 38 (коллинеарный штырь)",
        weight_kg=1.8,
        suggested_mount="Трубостойка на кровле, верхушка мачты",
        description="Круговая коллинеарная антенна 360° для базовых станций интернета вещей (NB-IoT, LoRaWAN, LTE Cat-M).",
    ),
    AntennaType(
        id="diamond_x50_omni",
        manufacturer="Diamond / Comet",
        model="Diamond X-50 Base Omni",
        category="Omni 360°",
        freq_mhz=430.0,
        freq_range="144 - 146 / 430 - 440 MHz (VHF/UHF)",
        gain_dbi=4.5,
        tx_power_w=50.0,
        beamwidth_h=360.0,
        beamwidth_v=40.0,
        tilt_range="Фиксированный круговой 0°",
        wall_loss_db=10.0,
        polarization="Linear Vertical",
        ports="1x SO-239 / N-Jack",
        dimensions_mm="1700 x 32 x 32 (фибергласс)",
        weight_kg=1.2,
        suggested_mount="Мачта на крыше, вершина опоры",
        description="Надёжная фиберглассовая двухдиапазонная коллинеарная антенна для радиомодемов, систем телеметрии и связи экстренных служб.",
    ),
    AntennaType(
        id="sirio_spo_multi",
        manufacturer="Sirio",
        model="Sirio SPO 790-2700 Multi-band",
        category="Omni 360°",
        freq_mhz=1800.0,
        freq_range="790 - 960 / 1710 - 2700 MHz",
        gain_dbi=9.0,
        tx_power_w=30.0,
        beamwidth_h=360.0,
        beamwidth_v=16.0,
        tilt_range="Круговой 0°",
        wall_loss_db=14.0,
        polarization="Linear Vertical",
        ports="1x N-Female",
        dimensions_mm="950 x 60 x 60",
        weight_kg=2.1,
        suggested_mount="Верхушка мачты, крыша павильона",
        description="Промышленная широкополосная всенаправленная антенна высокого усиления (9 дБи) для стационарных базовых станций 4G/LTE.",
    ),
    AntennaType(
        id="panorama_lpb_7_27",
        manufacturer="Panorama",
        model="LPB-7-27 Low-Profile",
        category="IoT/LPWAN",
        freq_mhz=900.0,
        freq_range="698 - 2700 MHz Multi-band",
        gain_dbi=5.0,
        tx_power_w=25.0,
        beamwidth_h=360.0,
        beamwidth_v=55.0,
        tilt_range="Вандалозащищённый купол",
        wall_loss_db=10.0,
        polarization="Vertical",
        ports="1x SMA-Male / N-Jack",
        dimensions_mm="82 x 82 x 48 (купол)",
        weight_kg=0.35,
        suggested_mount="Крыша павильона, шкаф автоматики",
        description="Низкопрофильная вандалоустойчивая антенна для компактных шлюзов телеметрии и Smart City.",
    ),
]


def init_antenna_db(conn: sqlite3.Connection):
    """Инициализирует таблицу типов антенн в SQLite."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS antenna_types (
                id TEXT PRIMARY KEY,
                manufacturer TEXT,
                model TEXT,
                category TEXT,
                freq_mhz REAL,
                freq_range TEXT,
                gain_dbi REAL,
                tx_power_w REAL,
                beamwidth_h REAL,
                beamwidth_v REAL,
                tilt_range TEXT,
                wall_loss_db REAL,
                polarization TEXT,
                ports TEXT,
                dimensions_mm TEXT,
                weight_kg REAL,
                suggested_mount TEXT,
                description TEXT
            )
        """)
        for a in BUILTIN_ANTENNAS:
            conn.execute("""
                INSERT OR REPLACE INTO antenna_types VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                a.id, a.manufacturer, a.model, a.category, a.freq_mhz, a.freq_range,
                a.gain_dbi, a.tx_power_w, a.beamwidth_h, a.beamwidth_v, a.tilt_range,
                a.wall_loss_db, a.polarization, a.ports, a.dimensions_mm, a.weight_kg,
                a.suggested_mount, a.description
            ))


def load_all_antennas(conn: sqlite3.Connection) -> list[AntennaType]:
    """Загружает все доступные типы антенн из БД."""
    init_antenna_db(conn)
    rows = conn.execute("SELECT * FROM antenna_types ORDER BY manufacturer, model").fetchall()
    res = []
    for r in rows:
        r_dict = dict(r) if hasattr(r, "keys") else {
            "id": r[0], "manufacturer": r[1], "model": r[2], "category": r[3],
            "freq_mhz": r[4], "freq_range": r[5], "gain_dbi": r[6], "tx_power_w": r[7],
            "beamwidth_h": r[8], "beamwidth_v": r[9], "tilt_range": r[10],
            "wall_loss_db": r[11], "polarization": r[12], "ports": r[13],
            "dimensions_mm": r[14], "weight_kg": r[15], "suggested_mount": r[16],
            "description": r[17],
        }
        res.append(AntennaType(**r_dict))
    return res


class AntennaCatalogDialog(QDialog):
    """Интерактивный каталог коммерческих антенн базовых станций."""

    antenna_selected = pyqtSignal(object)  # передаёт выбранный AntennaType

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Каталог антенн базовых станций (База типов)")
        self.resize(1000, 620)
        self.antennas: list[AntennaType] = load_all_antennas(self.conn)
        self.filtered: list[AntennaType] = list(self.antennas)
        self.selected_antenna: Optional[AntennaType] = None

        self._build_ui()
        self._populate_table()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Верхняя панель фильтров
        filter_box = QGroupBox("Поиск и фильтры")
        f_lay = QHBoxLayout(filter_box)
        f_lay.setContentsMargins(10, 8, 10, 8)
        f_lay.setSpacing(10)

        f_lay.addWidget(QLabel("Поиск:"))
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Модель, технология, производитель…")
        self.ed_search.textChanged.connect(self._apply_filters)
        f_lay.addWidget(self.ed_search, stretch=2)

        f_lay.addWidget(QLabel("Производитель:"))
        self.cmb_mfr = QComboBox()
        self.cmb_mfr.addItem("Все производители")
        mfrs = sorted({a.manufacturer for a in self.antennas})
        for m in mfrs:
            self.cmb_mfr.addItem(m)
        self.cmb_mfr.currentIndexChanged.connect(self._apply_filters)
        f_lay.addWidget(self.cmb_mfr, stretch=1)

        f_lay.addWidget(QLabel("Категория:"))
        self.cmb_cat = QComboBox()
        self.cmb_cat.addItem("Все категории")
        cats = sorted({a.category for a in self.antennas})
        for c in cats:
            self.cmb_cat.addItem(c)
        self.cmb_cat.currentIndexChanged.connect(self._apply_filters)
        f_lay.addWidget(self.cmb_cat, stretch=1)

        root.addWidget(filter_box)

        # Сплиттер: Таблица антенн слева, карточка характеристик справа
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Левая часть — таблица
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Производитель", "Модель", "Категория", "Частота", "Ку, дБи", "Ширина луча (H)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_table_select)
        self.table.itemDoubleClicked.connect(lambda _: self._on_choose())
        splitter.addWidget(self.table)

        # Правая часть — детальные характеристики
        detail_widget = QWidget()
        d_lay = QVBoxLayout(detail_widget)
        d_lay.setContentsMargins(10, 0, 0, 0)
        d_lay.setSpacing(8)

        d_lay.addWidget(QLabel("<b>Технические характеристики антенны</b>"))
        self.detail_browser = QTextBrowser()
        self.detail_browser.setOpenExternalLinks(False)
        d_lay.addWidget(self.detail_browser)

        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

        # Нижняя панель действий
        bottom = QHBoxLayout()
        self.lbl_count = QLabel("")
        bottom.addWidget(self.lbl_count)
        bottom.addStretch()

        self.btn_choose = QPushButton("✓ Выбрать")
        self.btn_choose.setProperty("primary", "true")
        self.btn_choose.setEnabled(False)
        self.btn_choose.setToolTip("Выбрать эту модель антенны для активной вышки или нового профиля")
        self.btn_choose.clicked.connect(self._on_choose)
        bottom.addWidget(self.btn_choose)

        self.btn_apply = QPushButton("Применить к вышке")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip("Применить характеристики антенны к текущей выбранной вышке")
        self.btn_apply.clicked.connect(self._on_choose)
        bottom.addWidget(self.btn_apply)

        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_close)

        root.addLayout(bottom)

    def _populate_table(self):
        self.table.setRowCount(len(self.filtered))
        for r, a in enumerate(self.filtered):
            self.table.setItem(r, 0, QTableWidgetItem(a.manufacturer))
            self.table.setItem(r, 1, QTableWidgetItem(a.model))
            self.table.setItem(r, 2, QTableWidgetItem(a.category))
            self.table.setItem(r, 3, QTableWidgetItem(f"{a.freq_mhz:.0f} МГц"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{a.gain_dbi:.1f} дБи"))
            self.table.setItem(r, 5, QTableWidgetItem(f"{a.beamwidth_h:.0f}°"))
        self.lbl_count.setText(f"Найдено моделей: {len(self.filtered)} из {len(self.antennas)}")
        if self.filtered:
            self.table.selectRow(0)

    def _apply_filters(self):
        q = self.ed_search.text().strip().lower()
        mfr = self.cmb_mfr.currentText()
        cat = self.cmb_cat.currentText()
        res = []
        for a in self.antennas:
            if mfr != "Все производители" and a.manufacturer != mfr:
                continue
            if cat != "Все категории" and a.category != cat:
                continue
            if q:
                blob = f"{a.manufacturer} {a.model} {a.category} {a.description} {a.freq_range}".lower()
                if q not in blob:
                    continue
            res.append(a)
        self.filtered = res
        self._populate_table()

    def _on_table_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.selected_antenna = None
            if hasattr(self, "btn_choose"):
                self.btn_choose.setEnabled(False)
            self.btn_apply.setEnabled(False)
            self.detail_browser.setHtml("<p style='color:#888'>Выберите антенну из списка</p>")
            return
        row = rows[0].row()
        if 0 <= row < len(self.filtered):
            self.selected_antenna = self.filtered[row]
            if hasattr(self, "btn_choose"):
                self.btn_choose.setEnabled(True)
            self.btn_apply.setEnabled(True)
            self._render_details(self.selected_antenna)

    def _render_details(self, a: AntennaType):
        eirp_w = a.tx_power_w * (10 ** (a.gain_dbi / 10.0))
        eirp_dbm = 10 * (eirp_w / 0.001) if eirp_w > 0 else 0
        html = f"""
        <style>
          body {{ font-family: system-ui, sans-serif; font-size: 12px; line-height: 1.4; color: #dcdcdc; }}
          h3 {{ margin: 0 0 6px 0; color: #3e8bff; font-size: 15px; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
          td {{ padding: 4px 6px; border-bottom: 1px solid rgba(255,255,255,0.08); }}
          td.label {{ color: #8a93a0; width: 42%; font-weight: 500; }}
          td.val {{ color: #ffffff; font-weight: 600; }}
          .desc {{ margin-top: 10px; padding: 8px; background: rgba(255,255,255,0.04); border-radius: 6px; font-style: italic; color: #c0c6cf; }}
        </style>
        <h3>{a.manufacturer} {a.model}</h3>
        <div style="color: #2ecc71; font-weight: 600;">{a.category}</div>
        <table>
          <tr><td class="label">Диапазон частот:</td><td class="val">{a.freq_range} ({a.freq_mhz:.0f} МГц)</td></tr>
          <tr><td class="label">Коэффициент усиления:</td><td class="val">{a.gain_dbi:.1f} дБи</td></tr>
          <tr><td class="label">Мощность передатчика:</td><td class="val">{a.tx_power_w:.0f} Вт</td></tr>
          <tr><td class="label">Ширина луча (H / V):</td><td class="val">{a.beamwidth_h:.0f}° / {a.beamwidth_v:.1f}°</td></tr>
          <tr><td class="label">Управление наклоном:</td><td class="val">{a.tilt_range}</td></tr>
          <tr><td class="label">Поляризация:</td><td class="val">{a.polarization}</td></tr>
          <tr><td class="label">Порты:</td><td class="val">{a.ports}</td></tr>
          <tr><td class="label">Потери в стенах (Wall Loss):</td><td class="val">{a.wall_loss_db:.1f} дБ</td></tr>
          <tr><td class="label">Габариты (В x Ш x Г):</td><td class="val">{a.dimensions_mm}</td></tr>
          <tr><td class="label">Масса:</td><td class="val">{a.weight_kg:.1f} кг</td></tr>
          <tr><td class="label">Рекомендуемый монтаж:</td><td class="val">{a.suggested_mount}</td></tr>
        </table>
        <div class="desc">{a.description}</div>
        """
        self.detail_browser.setHtml(html)

    def _on_choose(self):
        if self.selected_antenna:
            self.antenna_selected.emit(self.selected_antenna)
            self.accept()

    def _on_apply(self):
        self._on_choose()
