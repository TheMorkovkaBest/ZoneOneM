"""Генератор технических иллюстраций и схем антенн и вышек базовых станций.

Создаёт векторные SVG-изображения и растровые QPixmap/QIcon для всех промышленных моделей
антенн (Huawei, Kathrein, Ericsson, CommScope, Nokia, Tongyu, Diamond, Sirio, Ubiquiti,
MikroTik, Panorama) и опорных конструкций вышек (решётчатые башни, мачты, монополи).
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

_PIXMAP_CACHE: dict[tuple, Any] = {}
_ICON_CACHE: dict[tuple, Any] = {}


def _mfr_color(manufacturer: str) -> tuple[str, str]:
    """Возвращает цвета бренда (акцент, фон бейджа)."""
    m = (manufacturer or "").lower()
    if "huawei" in m:
        return "#ff3344", "#3a141a"
    if "ericsson" in m:
        return "#0082f0", "#0e2540"
    if "kathrein" in m:
        return "#00a0e9", "#0c283d"
    if "commscope" in m:
        return "#ff9900", "#3d270b"
    if "nokia" in m:
        return "#127fff", "#0d2547"
    if "tongyu" in m:
        return "#00c8b3", "#0b302c"
    if "ubiquiti" in m:
        return "#0070f3", "#0d2442"
    if "mikrotik" in m:
        return "#3b82f6", "#1c2b42"
    if "diamond" in m or "comet" in m:
        return "#f59e0b", "#36240d"
    if "sirio" in m:
        return "#10b981", "#0e3325"
    if "panorama" in m:
        return "#a855f7", "#2b1842"
    return "#3e8bff", "#162740"


def generate_antenna_svg(antenna: Any, width: int = 320, height: int = 180, dark_theme: bool = True) -> str:
    """Генерирует детальное векторное SVG-изображение для модели антенны."""
    # Безопасное экранирование строк
    mfr = html.escape(str(getattr(antenna, "manufacturer", "Antenna")))
    model = html.escape(str(getattr(antenna, "model", "Model")))
    cat = html.escape(str(getattr(antenna, "category", "")))
    freq_mhz = float(getattr(antenna, "freq_mhz", 1800.0))
    gain_dbi = float(getattr(antenna, "gain_dbi", 15.0))
    bw_h = float(getattr(antenna, "beamwidth_h", 65.0))
    bw_v = float(getattr(antenna, "beamwidth_v", 7.0))
    dims = html.escape(str(getattr(antenna, "dimensions_mm", "")))
    weight = float(getattr(antenna, "weight_kg", 20.0))
    ports = html.escape(str(getattr(antenna, "ports", "")))
    ant_id = getattr(antenna, "id", "")

    accent, badge_bg = _mfr_color(mfr)
    bg_color = "#151821" if dark_theme else "#f8fafc"
    grid_color = "#1f2433" if dark_theme else "#e2e8f0"
    text_main = "#ffffff" if dark_theme else "#0f172a"
    text_muted = "#8a96a8" if dark_theme else "#64748b"
    card_border = "#2a3144" if dark_theme else "#cbd5e1"

    # Специфический чертёж в зависимости от категории
    equipment_svg = _render_equipment_art(
        cat=cat,
        ant_id=ant_id,
        mfr=mfr,
        model=model,
        freq_mhz=freq_mhz,
        gain_dbi=gain_dbi,
        bw_h=bw_h,
        bw_v=bw_v,
        accent=accent,
        dark_theme=dark_theme,
    )

    # Бейджи характеристик
    gain_text = f"{gain_dbi:.1f} dBi"
    freq_text = f"{freq_mhz:.0f} MHz" if freq_mhz < 10000 else f"{freq_mhz/1000:.1f} GHz"
    beam_text = f"H: {bw_h:.0f}° · V: {bw_v:.1f}°"
    dim_short = dims.split("(")[0].strip()
    if len(dim_short) > 22:
        dim_short = dim_short[:20] + "…"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <pattern id="grid_{width}_{height}" width="16" height="16" patternUnits="userSpaceOnUse">
      <path d="M 16 0 L 0 0 0 16" fill="none" stroke="{grid_color}" stroke-width="0.7" opacity="0.6"/>
    </pattern>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{bg_color}"/>
      <stop offset="100%" stop-color="{'#0f1219' if dark_theme else '#edf2f7'}"/>
    </linearGradient>
    <linearGradient id="beamCyan" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#00e5ff" stop-opacity="0.45"/>
      <stop offset="50%" stop-color="#3a82f6" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#3a82f6" stop-opacity="0.0"/>
    </linearGradient>
    <linearGradient id="beamAmber" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#ffb300" stop-opacity="0.45"/>
      <stop offset="50%" stop-color="#ff6d00" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="#ff3d00" stop-opacity="0.0"/>
    </linearGradient>
    <linearGradient id="beamGreen" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#10b981" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="#06b6d4" stop-opacity="0.0"/>
    </linearGradient>
    <linearGradient id="metalChassis" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="35%" stop-color="#e2e8f0"/>
      <stop offset="70%" stop-color="#cbd5e1"/>
      <stop offset="100%" stop-color="#94a3b8"/>
    </linearGradient>
    <linearGradient id="heatsinkFins" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#475569"/>
      <stop offset="50%" stop-color="#334155"/>
      <stop offset="100%" stop-color="#1e293b"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- Фон карточки -->
  <rect width="{width}" height="{height}" rx="8" fill="url(#bgGrad)"/>
  <rect width="{width}" height="{height}" rx="8" fill="url(#grid_{width}_{height})"/>
  <rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="7.5" fill="none" stroke="{card_border}" stroke-width="1"/>

  <!-- Верхний заголовок: Производитель и Модель -->
  <g transform="translate(12, 16)">
    <!-- Бренд-бейдж -->
    <rect x="0" y="0" width="70" height="18" rx="4" fill="{badge_bg}" stroke="{accent}" stroke-width="1"/>
    <text x="35" y="13" fill="{accent}" font-family="system-ui, -apple-system, sans-serif" font-size="9.5" font-weight="700" text-anchor="middle" letter-spacing="0.5">{mfr.upper()}</text>

    <!-- Название модели -->
    <text x="78" y="14" fill="{text_main}" font-family="system-ui, -apple-system, sans-serif" font-size="12" font-weight="700">{model}</text>
  </g>

  <!-- Правый верхний бейдж: Частота и Ку -->
  <g transform="translate({width - 12}, 16)">
    <rect x="-110" y="0" width="110" height="18" rx="4" fill="{'rgba(255,255,255,0.06)' if dark_theme else 'rgba(0,0,0,0.05)'}" stroke="{card_border}" stroke-width="1"/>
    <circle cx="-98" cy="9" r="3" fill="{accent}"/>
    <text x="-90" y="13" fill="{text_main}" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="600">{freq_text} · {gain_text}</text>
  </g>

  <!-- Центральная графическая зона чертежа оборудования -->
  <g transform="translate(0, 10)">
    {equipment_svg}
  </g>

  <!-- Нижняя информационная строка -->
  <g transform="translate(12, {height - 12})">
    <!-- Категория слева -->
    <text x="0" y="0" fill="{text_muted}" font-family="system-ui, -apple-system, sans-serif" font-size="9.5" font-weight="500">{cat} · {beam_text}</text>

    <!-- Габариты и масса справа -->
    <text x="{width - 24}" y="0" fill="{text_muted}" font-family="system-ui, -apple-system, sans-serif" font-size="9.5" font-weight="500" text-anchor="end">{dim_short} · {weight:.1f} кг</text>
  </g>
</svg>"""


def _render_equipment_art(
    cat: str,
    ant_id: str,
    mfr: str,
    model: str,
    freq_mhz: float,
    gain_dbi: float,
    bw_h: float,
    bw_v: float,
    accent: str,
    dark_theme: bool,
) -> str:
    """Генерирует SVG-элементы конкретного типа оборудования."""
    c_lower = cat.lower()

    if "massive mimo" in c_lower or "aau" in ant_id:
        return _art_massive_mimo(accent, bw_h, dark_theme)
    elif "macro panel" in c_lower or "macro" in c_lower and "small" not in c_lower:
        return _art_macro_panel(accent, bw_h, dark_theme)
    elif "omni" in c_lower or bw_h >= 300:
        if "dome" in ant_id or "indoor" in ant_id:
            return _art_omni_dome(accent, dark_theme)
        return _art_omni_collinear(accent, dark_theme)
    elif "small cell" in c_lower or "micro" in c_lower:
        if "dot" in ant_id or "lampsite" in ant_id:
            return _art_indoor_dot(accent, dark_theme)
        return _art_small_cell_box(accent, dark_theme)
    elif "wisp" in c_lower or "бшпд" in c_lower or "horn" in ant_id:
        if "horn" in ant_id:
            return _art_horn_antenna(accent, dark_theme)
        return _art_wisp_sector(accent, dark_theme)
    elif "iot" in c_lower or "lpwan" in c_lower or "puck" in ant_id or "dome" in c_lower:
        return _art_iot_puck(accent, dark_theme)
    else:
        return _art_macro_panel(accent, bw_h, dark_theme)


def _art_massive_mimo(accent: str, bw_h: float, dark_theme: bool) -> str:
    """Отрисовка 5G Active Antenna Unit (AAU / AIR) Massive MIMO."""
    return f"""
    <!-- Опорная труба мачты -->
    <rect x="42" y="24" width="8" height="114" rx="2" fill="#475569" stroke="#334155" stroke-width="1"/>
    <!-- Монтажные кронштейны трубы -->
    <rect x="38" y="38" width="16" height="8" rx="1.5" fill="#64748b"/>
    <rect x="38" y="104" width="16" height="8" rx="1.5" fill="#64748b"/>
    <line x1="50" y1="42" x2="68" y2="46" stroke="#94a3b8" stroke-width="3" stroke-linecap="round"/>
    <line x1="50" y1="108" x2="68" y2="104" stroke="#94a3b8" stroke-width="3" stroke-linecap="round"/>

    <!-- Радиатор охлаждения (ребра сзади) -->
    <rect x="64" y="32" width="12" height="88" rx="2" fill="url(#heatsinkFins)"/>
    <line x1="68" y1="36" x2="68" y2="116" stroke="#64748b" stroke-width="1"/>
    <line x1="72" y1="36" x2="72" y2="116" stroke="#64748b" stroke-width="1"/>

    <!-- Основной корпус AAU с радиопрозрачным колпаком (Radome) -->
    <rect x="74" y="28" width="56" height="96" rx="4" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <!-- Внутренняя матрица фазированной решётки 64T64R (MIMO patch array) -->
    <g fill="#94a3b8" opacity="0.75">
      <!-- 4 столбца по 6 патч-элементов -->
      <circle cx="85" cy="42" r="2.2"/><circle cx="95" cy="42" r="2.2"/><circle cx="105" cy="42" r="2.2"/><circle cx="115" cy="42" r="2.2"/>
      <circle cx="85" cy="54" r="2.2"/><circle cx="95" cy="54" r="2.2"/><circle cx="105" cy="54" r="2.2"/><circle cx="115" cy="54" r="2.2"/>
      <circle cx="85" cy="66" r="2.2"/><circle cx="95" cy="66" r="2.2"/><circle cx="105" cy="66" r="2.2"/><circle cx="115" cy="66" r="2.2"/>
      <circle cx="85" cy="78" r="2.2"/><circle cx="95" cy="78" r="2.2"/><circle cx="105" cy="78" r="2.2"/><circle cx="115" cy="78" r="2.2"/>
      <circle cx="85" cy="90" r="2.2"/><circle cx="95" cy="90" r="2.2"/><circle cx="105" cy="90" r="2.2"/><circle cx="115" cy="90" r="2.2"/>
      <circle cx="85" cy="102" r="2.2"/><circle cx="95" cy="102" r="2.2"/><circle cx="105" cy="102" r="2.2"/><circle cx="115" cy="102" r="2.2"/>
    </g>

    <!-- Индикаторы состояния (LEDs) -->
    <circle cx="82" cy="116" r="1.8" fill="#10b981"/>
    <circle cx="88" cy="116" r="1.8" fill="#10b981"/>
    <circle cx="94" cy="116" r="1.8" fill="#3b82f6"/>
    <!-- Оптические порты eCPRI / Питание снизу -->
    <rect x="104" y="121" width="6" height="5" rx="1" fill="#f59e0b"/>
    <rect x="113" y="121" width="8" height="5" rx="1" fill="#334155"/>

    <!-- Диаграмма лучей Dynamic Beamforming (5G Active Array) -->
    <g transform="translate(130, 26)">
      <!-- Веер лучей -->
      <path d="M 0 50 L 150 10 A 160 160 0 0 1 150 90 Z" fill="url(#beamCyan)"/>
      <path d="M 0 50 L 130 30" stroke="#00e5ff" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.8"/>
      <path d="M 0 50 L 145 50" stroke="#00e5ff" stroke-width="1.8" opacity="0.9"/>
      <path d="M 0 50 L 130 70" stroke="#00e5ff" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.8"/>

      <!-- Волновые фронты -->
      <path d="M 50 36 A 55 55 0 0 1 50 64" fill="none" stroke="#00e5ff" stroke-width="1.2" opacity="0.7"/>
      <path d="M 90 26 A 95 95 0 0 1 90 74" fill="none" stroke="#00e5ff" stroke-width="1.2" opacity="0.5"/>
      <path d="M 130 16 A 135 135 0 0 1 130 84" fill="none" stroke="#00e5ff" stroke-width="1.2" opacity="0.3"/>

      <!-- Метка технологии -->
      <rect x="65" y="4" width="76" height="15" rx="3" fill="rgba(0,229,255,0.12)" stroke="#00e5ff" stroke-width="0.8"/>
      <text x="103" y="15" fill="#00e5ff" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">DYNAMIC 64T64R</text>
    </g>
    """


def _art_macro_panel(accent: str, bw_h: float, dark_theme: bool) -> str:
    """Отрисовка классической секторной панельной антенны 4G/5G Macro Panel."""
    half_angle = min(45, max(15, bw_h / 2.0))
    y_spread = half_angle * 0.95
    return f"""
    <!-- Опорная стойка -->
    <rect x="46" y="20" width="7" height="122" rx="2" fill="#475569" stroke="#334155" stroke-width="1"/>
    <!-- Крепёжные хомуты с регулировкой наклона RET -->
    <rect x="42" y="36" width="15" height="7" rx="1" fill="#64748b"/>
    <rect x="42" y="112" width="15" height="7" rx="1" fill="#64748b"/>
    <line x1="53" y1="39" x2="72" y2="44" stroke="#94a3b8" stroke-width="2.5"/>
    <line x1="53" y1="115" x2="72" y2="110" stroke="#94a3b8" stroke-width="2.5"/>

    <!-- Радиопрозрачный стеклопластиковый обтекатель антенны (Radome) -->
    <rect x="72" y="22" width="34" height="112" rx="3" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <!-- Продольное ребро жёсткости -->
    <line x1="89" y1="26" x2="89" y2="128" stroke="#cbd5e1" stroke-width="1.5"/>

    <!-- Нижняя панель портов 4.3-10 / 7/16 DIN -->
    <rect x="75" y="132" width="28" height="6" rx="1" fill="#334155"/>
    <!-- Коаксиальные разъёмы (цветная маркировка диапазонов) -->
    <circle cx="79" cy="135" r="1.5" fill="#ef4444"/>
    <circle cx="84" cy="135" r="1.5" fill="#3b82f6"/>
    <circle cx="89" cy="135" r="1.5" fill="#eab308"/>
    <circle cx="94" cy="135" r="1.5" fill="#10b981"/>
    <circle cx="99" cy="135" r="1.5" fill="#a855f7"/>

    <!-- Сектор излучения (Азимутальная диаграмма направленности) -->
    <g transform="translate(106, 26)">
      <path d="M 0 52 L 165 {52 - y_spread} A 175 175 0 0 1 165 {52 + y_spread} Z" fill="url(#beamAmber)"/>
      <line x1="0" y1="52" x2="160" y2="52" stroke="#ffb300" stroke-width="1.5" stroke-dasharray="4,3"/>

      <!-- Волновые фронты сектора -->
      <path d="M 60 {52 - y_spread * 0.38} A 65 65 0 0 1 60 {52 + y_spread * 0.38}" fill="none" stroke="#ff9800" stroke-width="1.2" opacity="0.7"/>
      <path d="M 110 {52 - y_spread * 0.68} A 115 115 0 0 1 110 {52 + y_spread * 0.68}" fill="none" stroke="#ff9800" stroke-width="1.2" opacity="0.5"/>
      <path d="M 155 {52 - y_spread * 0.95} A 160 160 0 0 1 155 {52 + y_spread * 0.95}" fill="none" stroke="#ff9800" stroke-width="1.2" opacity="0.3"/>

      <!-- Ширина луча бейдж -->
      <rect x="75" y="6" width="70" height="15" rx="3" fill="rgba(255,152,0,0.12)" stroke="#ff9800" stroke-width="0.8"/>
      <text x="110" y="17" fill="#ff9800" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">СЕКТОР {bw_h:.0f}°</text>
    </g>
    """


def _art_omni_collinear(accent: str, dark_theme: bool) -> str:
    """Отрисовка коллинеарной круговой антенны (Omni 360° whip/fiberglass)."""
    return f"""
    <!-- Опорная труба крепления снизу -->
    <rect x="146" y="112" width="8" height="34" rx="1.5" fill="#475569" stroke="#334155" stroke-width="1"/>
    <!-- U-образные монтажные скобы -->
    <rect x="143" y="118" width="14" height="4" rx="1" fill="#94a3b8"/>
    <rect x="143" y="132" width="14" height="4" rx="1" fill="#94a3b8"/>

    <!-- Анодированное металлическое основание с разъёмом N-типа -->
    <rect x="147" y="92" width="6" height="20" rx="1" fill="url(#metalChassis)" stroke="#64748b" stroke-width="1"/>

    <!-- Противовесы (Ground plane radials) под 45 градусов -->
    <line x1="150" y1="102" x2="114" y2="124" stroke="#94a3b8" stroke-width="2.5" stroke-linecap="round"/>
    <line x1="150" y1="102" x2="186" y2="124" stroke="#94a3b8" stroke-width="2.5" stroke-linecap="round"/>
    <line x1="150" y1="102" x2="150" y2="126" stroke="#64748b" stroke-width="2" stroke-linecap="round"/>

    <!-- Тонкий стеклопластиковый штырь антенны (Fiberglass radome) -->
    <rect x="148" y="24" width="4" height="70" rx="1.5" fill="#f8fafc" stroke="#94a3b8" stroke-width="0.8"/>
    <circle cx="150" cy="24" r="2.5" fill="#e2e8f0"/>

    <!-- Круговая тороидальная диаграмма направленности (360° Omni donut) -->
    <g opacity="0.85">
      <!-- Левый лепесток -->
      <path d="M 148 58 C 110 38 60 44 40 58 C 60 72 110 78 148 58 Z" fill="url(#beamGreen)"/>
      <!-- Правый лепесток -->
      <path d="M 152 58 C 190 38 240 44 260 58 C 240 72 190 78 152 58 Z" fill="url(#beamGreen)"/>

      <!-- Концентрические круги 360° -->
      <ellipse cx="150" cy="58" rx="55" ry="16" fill="none" stroke="#10b981" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.7"/>
      <ellipse cx="150" cy="58" rx="95" ry="26" fill="none" stroke="#10b981" stroke-width="1.0" opacity="0.4"/>
    </g>

    <rect x="105" y="28" width="90" height="15" rx="3" fill="rgba(16,185,129,0.12)" stroke="#10b981" stroke-width="0.8"/>
    <text x="150" y="39" fill="#10b981" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">КРУГОВОЙ 360°</text>
    """


def _art_omni_dome(accent: str, dark_theme: bool) -> str:
    """Отрисовка потолочной купольной антенны (Indoor Dome DAS)."""
    return f"""
    <!-- Потолочная плита -->
    <rect x="70" y="38" width="160" height="6" rx="1" fill="#475569"/>

    <!-- Купол антенны -->
    <path d="M 100 44 Q 150 96 200 44 Z" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <circle cx="150" cy="44" r="5" fill="#334155"/>
    <!-- Коаксиальный кабель уходит в потолок -->
    <path d="M 150 38 L 150 26 Q 150 20 165 20 L 195 20" fill="none" stroke="#0f172a" stroke-width="2.5"/>

    <!-- Зона направленности вниз в помещение (полусфера) -->
    <g opacity="0.8">
      <path d="M 115 54 Q 150 120 185 54" fill="none" stroke="#06b6d4" stroke-width="1.5" stroke-dasharray="4,3"/>
      <path d="M 90 62 Q 150 145 210 62" fill="none" stroke="#06b6d4" stroke-width="1.2" opacity="0.6"/>
      <path d="M 65 72 Q 150 170 235 72" fill="none" stroke="#06b6d4" stroke-width="1.0" opacity="0.3"/>
    </g>

    <rect x="105" y="105" width="90" height="15" rx="3" fill="rgba(6,182,212,0.12)" stroke="#06b6d4" stroke-width="0.8"/>
    <text x="150" y="116" fill="#06b6d4" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">INDOOR DOME 360°</text>
    """


def _art_indoor_dot(accent: str, dark_theme: bool) -> str:
    """Отрисовка микросоты Radio Dot / LampSite pRRU."""
    return f"""
    <!-- Опорная плоскость (потолок / стена) -->
    <line x1="60" y1="40" x2="240" y2="40" stroke="#475569" stroke-width="3" stroke-dasharray="6,4"/>

    <!-- Компактный корпус Dot / pRRU -->
    <circle cx="150" cy="74" r="28" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.5"/>
    <circle cx="150" cy="74" r="18" fill="{'#1e293b' if dark_theme else '#f1f5f9'}" stroke="#cbd5e1" stroke-width="1"/>

    <!-- Светящееся кольцо-индикатор состояния (LED Halo) -->
    <circle cx="150" cy="74" r="10" fill="none" stroke="#00e5ff" stroke-width="2.5" filter="url(#glow)"/>
    <circle cx="150" cy="74" r="4" fill="#00e5ff"/>

    <!-- Волны микросоты -->
    <ellipse cx="150" cy="80" rx="55" ry="24" fill="none" stroke="#00e5ff" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.7"/>
    <ellipse cx="150" cy="84" rx="90" ry="38" fill="none" stroke="#00e5ff" stroke-width="1.0" opacity="0.4"/>

    <rect x="100" y="125" width="100" height="15" rx="3" fill="rgba(0,229,255,0.12)" stroke="#00e5ff" stroke-width="0.8"/>
    <text x="150" y="136" fill="#00e5ff" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">ACTIVE RADIO DOT</text>
    """


def _art_small_cell_box(accent: str, dark_theme: bool) -> str:
    """Отрисовка уличной микросоты (EasyMacro / Street Macro)."""
    return f"""
    <!-- Фонарный столб / опора уличного освещения -->
    <rect x="52" y="22" width="12" height="118" rx="2" fill="#475569" stroke="#334155" stroke-width="1"/>
    <!-- Компактный обхватывающий хомут -->
    <rect x="48" y="44" width="20" height="6" rx="1.5" fill="#94a3b8"/>
    <rect x="48" y="98" width="20" height="6" rx="1.5" fill="#94a3b8"/>

    <!-- Корпус Street Macro / EasyMacro скруглённый -->
    <rect x="70" y="36" width="38" height="74" rx="8" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <!-- Встроенный радиатор охлаждения сзади -->
    <rect x="65" y="44" width="6" height="58" rx="1" fill="#334155"/>
    <!-- Дизайнерский обтекатель -->
    <path d="M 80 42 Q 102 73 80 104" fill="none" stroke="#cbd5e1" stroke-width="1.5"/>

    <!-- Светодиод работы -->
    <circle cx="89" cy="98" r="2" fill="#10b981"/>

    <!-- Микросектор покрытия -->
    <g transform="translate(108, 30)">
      <path d="M 0 43 L 160 12 A 165 165 0 0 1 160 74 Z" fill="url(#beamCyan)"/>
      <line x1="0" y1="43" x2="150" y2="43" stroke="#00e5ff" stroke-width="1.5" stroke-dasharray="3,3"/>
      <path d="M 60 30 A 65 65 0 0 1 60 56" fill="none" stroke="#00e5ff" stroke-width="1.2" opacity="0.7"/>
      <path d="M 110 20 A 115 115 0 0 1 110 66" fill="none" stroke="#00e5ff" stroke-width="1.0" opacity="0.4"/>
    </g>

    <rect x="180" y="32" width="85" height="15" rx="3" fill="rgba(0,229,255,0.12)" stroke="#00e5ff" stroke-width="0.8"/>
    <text x="222" y="43" fill="#00e5ff" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">STREET MICRO</text>
    """


def _art_horn_antenna(accent: str, dark_theme: bool) -> str:
    """Отрисовка рупорной антенны (PrismStation Horn)."""
    return f"""
    <!-- Стойка -->
    <rect x="46" y="24" width="7" height="114" rx="1.5" fill="#475569"/>
    <!-- Шарнирный кронштейн 360° -->
    <circle cx="65" cy="74" r="8" fill="#64748b"/>
    <rect x="53" y="70" width="14" height="8" rx="1" fill="#94a3b8"/>

    <!-- Радиомодуль сзади -->
    <rect x="70" y="60" width="18" height="28" rx="2" fill="#334155" stroke="#475569" stroke-width="1"/>

    <!-- Рупорная антенна (Horn cone) -->
    <path d="M 88 64 L 126 44 L 126 104 L 88 84 Z" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <ellipse cx="126" cy="74" rx="5" ry="30" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>

    <!-- Сфокусированный узкий луч 45° -->
    <g transform="translate(131, 26)">
      <path d="M 0 48 L 140 18 A 145 145 0 0 1 140 78 Z" fill="url(#beamCyan)"/>
      <line x1="0" y1="48" x2="135" y2="48" stroke="#00e5ff" stroke-width="1.5" stroke-dasharray="3,3"/>
      <path d="M 50 37 A 55 55 0 0 1 50 59" fill="none" stroke="#00e5ff" stroke-width="1.2" opacity="0.8"/>
      <path d="M 95 28 A 100 100 0 0 1 95 68" fill="none" stroke="#00e5ff" stroke-width="1.0" opacity="0.5"/>
    </g>

    <rect x="180" y="30" width="90" height="15" rx="3" fill="rgba(0,229,255,0.12)" stroke="#00e5ff" stroke-width="0.8"/>
    <text x="225" y="41" fill="#00e5ff" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">HORN SYMMETRIC 45°</text>
    """


def _art_wisp_sector(accent: str, dark_theme: bool) -> str:
    """Отрисовка БШПД секторной точки доступа (MikroTik / Ubiquiti airMAX)."""
    return f"""
    <!-- Трубостойка -->
    <rect x="46" y="24" width="7" height="114" rx="1.5" fill="#475569"/>
    <!-- Хомут крепления -->
    <rect x="42" y="50" width="14" height="6" rx="1" fill="#94a3b8"/>
    <rect x="42" y="94" width="14" height="6" rx="1" fill="#94a3b8"/>
    <line x1="53" y1="53" x2="68" y2="58" stroke="#94a3b8" stroke-width="2"/>
    <line x1="53" y1="97" x2="68" y2="92" stroke="#94a3b8" stroke-width="2"/>

    <!-- Металлический экран и панель -->
    <rect x="68" y="32" width="24" height="86" rx="2" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <!-- Интегрированный радиобокс сзади -->
    <rect x="62" y="58" width="7" height="34" rx="1" fill="#334155"/>

    <!-- Сектор излучения -->
    <g transform="translate(92, 26)">
      <path d="M 0 49 L 160 12 A 165 165 0 0 1 160 86 Z" fill="url(#beamAmber)"/>
      <line x1="0" y1="49" x2="155" y2="49" stroke="#ffb300" stroke-width="1.5" stroke-dasharray="3,3"/>
      <path d="M 60 35 A 65 65 0 0 1 60 63" fill="none" stroke="#ff9800" stroke-width="1.2" opacity="0.7"/>
      <path d="M 110 23 A 115 115 0 0 1 110 75" fill="none" stroke="#ff9800" stroke-width="1.0" opacity="0.4"/>
    </g>

    <rect x="180" y="30" width="85" height="15" rx="3" fill="rgba(255,152,0,0.12)" stroke="#ff9800" stroke-width="0.8"/>
    <text x="222" y="41" fill="#ff9800" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">WISP SECTOR 5GHz</text>
    """


def _art_iot_puck(accent: str, dark_theme: bool) -> str:
    """Отрисовка компактной низкопрофильной антенны IoT/LPWAN (Panorama Puck)."""
    return f"""
    <!-- Панель монтажа (крыша шкафа / купол) -->
    <line x1="50" y1="100" x2="250" y2="100" stroke="#475569" stroke-width="3"/>
    <!-- Резьбовой шток снизу -->
    <rect x="144" y="100" width="12" height="22" rx="1" fill="#f59e0b" stroke="#b45309" stroke-width="1"/>
    <!-- Коаксиальный кабель RG-174 -->
    <path d="M 150 122 L 150 136 Q 150 144 165 144 L 210 144" fill="none" stroke="#0f172a" stroke-width="2.5"/>

    <!-- Вандалозащищённый корпус «шайба / акулий плавник» -->
    <path d="M 105 100 Q 150 52 195 100 Z" fill="#1e293b" stroke="#475569" stroke-width="1.5"/>
    <ellipse cx="150" cy="100" rx="45" ry="3" fill="#0f172a"/>

    <!-- Радиоволны телеметрии во все стороны -->
    <g opacity="0.8">
      <ellipse cx="150" cy="78" rx="45" ry="16" fill="none" stroke="#a855f7" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.8"/>
      <ellipse cx="150" cy="74" rx="75" ry="26" fill="none" stroke="#a855f7" stroke-width="1.0" opacity="0.5"/>
      <ellipse cx="150" cy="70" rx="105" ry="36" fill="none" stroke="#a855f7" stroke-width="0.8" opacity="0.3"/>
    </g>

    <rect x="100" y="28" width="100" height="15" rx="3" fill="rgba(168,85,247,0.12)" stroke="#a855f7" stroke-width="0.8"/>
    <text x="150" y="39" fill="#a855f7" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">LPWAN / IOT 360°</text>
    """


def generate_structure_svg(spec: Any, width: int = 320, height: int = 180, dark_theme: bool = True) -> str:
    """Генерирует чертёж несущей конструкции вышки (решётчатая башня, мачта, монополь)."""
    name = html.escape(str(getattr(spec, "name", "Опора")))
    kind = str(getattr(spec, "kind", "lattice")).lower()
    h_m = float(getattr(spec, "height", 30.0))
    desc = html.escape(str(getattr(spec, "description", "")))

    bg_color = "#151821" if dark_theme else "#f8fafc"
    grid_color = "#1f2433" if dark_theme else "#e2e8f0"
    text_main = "#ffffff" if dark_theme else "#0f172a"
    text_muted = "#8a96a8" if dark_theme else "#64748b"
    card_border = "#2a3144" if dark_theme else "#cbd5e1"

    if "guyed" in kind:
        kind_title = "МАЧТА НА ОТТЯЖКАХ"
        accent = "#38bdf8"
        struct_art = _art_struct_guyed()
    elif "monopole" in kind:
        kind_title = "МОНОПОЛЬ (ТРУБЧАТАЯ ОПОРА)"
        accent = "#a78bfa"
        struct_art = _art_struct_monopole()
    elif "pole" in kind:
        kind_title = "ТРУБОСТОЙКА / СТОЛБ"
        accent = "#fbbf24"
        struct_art = _art_struct_pole()
    elif "roof" in kind:
        kind_title = "КРЫШНАЯ ТРУБОСТОЙКА"
        accent = "#f43f5e"
        struct_art = _art_struct_roof()
    else:
        kind_title = "РЕШЁТЧАТАЯ БАШНЯ"
        accent = "#34d399"
        struct_art = _art_struct_lattice()

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <defs>
    <pattern id="s_grid_{width}_{height}" width="16" height="16" patternUnits="userSpaceOnUse">
      <path d="M 16 0 L 0 0 0 16" fill="none" stroke="{grid_color}" stroke-width="0.7" opacity="0.6"/>
    </pattern>
    <linearGradient id="s_bgGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{bg_color}"/>
      <stop offset="100%" stop-color="{'#0f1219' if dark_theme else '#edf2f7'}"/>
    </linearGradient>
    <linearGradient id="metalChassis" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="35%" stop-color="#e2e8f0"/>
      <stop offset="70%" stop-color="#cbd5e1"/>
      <stop offset="100%" stop-color="#94a3b8"/>
    </linearGradient>
  </defs>

  <rect width="{width}" height="{height}" rx="8" fill="url(#s_bgGrad)"/>
  <rect width="{width}" height="{height}" rx="8" fill="url(#s_grid_{width}_{height})"/>
  <rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="7.5" fill="none" stroke="{card_border}" stroke-width="1"/>

  <!-- Заголовок -->
  <g transform="translate(12, 16)">
    <rect x="0" y="0" width="80" height="18" rx="4" fill="{'rgba(255,255,255,0.06)' if dark_theme else 'rgba(0,0,0,0.05)'}" stroke="{accent}" stroke-width="1"/>
    <text x="40" y="13" fill="{accent}" font-family="system-ui, sans-serif" font-size="8.5" font-weight="700" text-anchor="middle">{kind_title.split()[0]}</text>
    <text x="88" y="14" fill="{text_main}" font-family="system-ui, sans-serif" font-size="12" font-weight="700">{name}</text>
  </g>

  <!-- Высота бейдж -->
  <g transform="translate({width - 12}, 16)">
    <rect x="-80" y="0" width="80" height="18" rx="4" fill="{'rgba(255,255,255,0.06)' if dark_theme else 'rgba(0,0,0,0.05)'}" stroke="{card_border}" stroke-width="1"/>
    <circle cx="-70" cy="9" r="3" fill="{accent}"/>
    <text x="-62" y="13" fill="{text_main}" font-family="system-ui, sans-serif" font-size="10" font-weight="600">H = {h_m:.0f} м</text>
  </g>

  <!-- Рисунок конструкции -->
  <g transform="translate(0, 10)">
    {struct_art}
  </g>

  <!-- Подвал карточки -->
  <g transform="translate(12, {height - 12})">
    <text x="0" y="0" fill="{text_muted}" font-family="system-ui, sans-serif" font-size="9.5" font-weight="500">{kind_title} · {desc[:40] if len(desc)>40 else desc}</text>
  </g>
</svg>"""


def _art_struct_lattice() -> str:
    """Чертёж стальной решётчатой четырёхгранной башни."""
    return """
    <!-- Земля / фундамент -->
    <line x1="80" y1="138" x2="240" y2="138" stroke="#475569" stroke-width="2"/>
    <rect x="126" y="138" width="14" height="4" fill="#334155"/>
    <rect x="180" y="138" width="14" height="4" fill="#334155"/>

    <!-- Пояса башни (сужающиеся к верху) -->
    <!-- Левая нога: (130, 138) -> (154, 38) -->
    <!-- Правая нога: (190, 138) -> (166, 38) -->
    <line x1="130" y1="138" x2="154" y2="38" stroke="#94a3b8" stroke-width="2.5"/>
    <line x1="190" y1="138" x2="166" y2="38" stroke="#94a3b8" stroke-width="2.5"/>

    <!-- Горизонтальные диафрагмы и X-раскосы решётки -->
    <!-- Ярус 1: y=118 -->
    <line x1="135" y1="118" x2="185" y2="118" stroke="#64748b" stroke-width="1.2"/>
    <line x1="130" y1="138" x2="185" y2="118" stroke="#64748b" stroke-width="1"/>
    <line x1="190" y1="138" x2="135" y2="118" stroke="#64748b" stroke-width="1"/>

    <!-- Ярус 2: y=98 -->
    <line x1="140" y1="98" x2="180" y2="98" stroke="#64748b" stroke-width="1.2"/>
    <line x1="135" y1="118" x2="180" y2="98" stroke="#64748b" stroke-width="1"/>
    <line x1="185" y1="118" x2="140" y2="98" stroke="#64748b" stroke-width="1"/>

    <!-- Ярус 3: y=78 -->
    <line x1="145" y1="78" x2="175" y2="78" stroke="#64748b" stroke-width="1.2"/>
    <line x1="140" y1="98" x2="175" y2="78" stroke="#64748b" stroke-width="1"/>
    <line x1="180" y1="98" x2="145" y2="78" stroke="#64748b" stroke-width="1"/>

    <!-- Ярус 4: y=58 -->
    <line x1="149" y1="58" x2="171" y2="58" stroke="#64748b" stroke-width="1.2"/>
    <line x1="145" y1="78" x2="171" y2="58" stroke="#64748b" stroke-width="1"/>
    <line x1="175" y1="78" x2="149" y2="58" stroke="#64748b" stroke-width="1"/>

    <!-- Верхняя площадка обслуживания (Platform) -->
    <rect x="146" y="36" width="28" height="3" fill="#cbd5e1"/>
    <line x1="146" y1="36" x2="146" y2="28" stroke="#94a3b8" stroke-width="1.2"/>
    <line x1="174" y1="36" x2="174" y2="28" stroke="#94a3b8" stroke-width="1.2"/>
    <line x1="144" y1="28" x2="176" y2="28" stroke="#94a3b8" stroke-width="1.2"/>

    <!-- Антенны на верхушке башни -->
    <rect x="142" y="24" width="4" height="14" rx="1" fill="#38bdf8"/>
    <rect x="174" y="24" width="4" height="14" rx="1" fill="#38bdf8"/>
    <!-- Молниеотвод -->
    <line x1="160" y1="28" x2="160" y2="16" stroke="#f1f5f9" stroke-width="1.5"/>
    <circle cx="160" cy="15" r="1.5" fill="#f59e0b"/>
    """


def _art_struct_guyed() -> str:
    """Чертёж мачты на оттяжках."""
    return """
    <!-- Земля и фундаменты оттяжек -->
    <line x1="50" y1="138" x2="270" y2="138" stroke="#475569" stroke-width="2"/>
    <rect x="65" y="138" width="16" height="5" fill="#334155"/>
    <rect x="239" y="138" width="16" height="5" fill="#334155"/>
    <circle cx="160" cy="138" r="4" fill="#64748b"/>

    <!-- Центральный ствол мачты -->
    <rect x="158" y="26" width="4" height="112" fill="#cbd5e1" stroke="#94a3b8" stroke-width="0.8"/>

    <!-- Стальные оттяжки 1-й ярус -->
    <line x1="160" y1="105" x2="73" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>
    <line x1="160" y1="105" x2="247" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>

    <!-- Стальные оттяжки 2-й ярус -->
    <line x1="160" y1="70" x2="73" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>
    <line x1="160" y1="70" x2="247" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>

    <!-- Стальные оттяжки 3-й ярус (верхний) -->
    <line x1="160" y1="36" x2="73" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>
    <line x1="160" y1="36" x2="247" y2="138" stroke="#38bdf8" stroke-width="1.2" opacity="0.8"/>

    <!-- Антенны на вершине мачты -->
    <rect x="153" y="24" width="3" height="12" rx="0.5" fill="#38bdf8"/>
    <rect x="164" y="24" width="3" height="12" rx="0.5" fill="#38bdf8"/>
    <line x1="160" y1="26" x2="160" y2="16" stroke="#f1f5f9" stroke-width="1.5"/>
    """


def _art_struct_monopole() -> str:
    """Чертёж монополя (конической трубчатой опоры)."""
    return """
    <!-- Фундамент -->
    <line x1="90" y1="138" x2="230" y2="138" stroke="#475569" stroke-width="2"/>
    <rect x="146" y="136" width="28" height="6" fill="#334155"/>

    <!-- Конический ствол монополя -->
    <polygon points="152,136 168,136 163,34 157,34" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1.2"/>
    <!-- Ходовые скобы / ступени лестницы -->
    <line x1="164" y1="120" x2="168" y2="120" stroke="#64748b" stroke-width="1.5"/>
    <line x1="163" y1="100" x2="167" y2="100" stroke="#64748b" stroke-width="1.5"/>
    <line x1="162" y1="80" x2="166" y2="80" stroke="#64748b" stroke-width="1.5"/>
    <line x1="161" y1="60" x2="165" y2="60" stroke="#64748b" stroke-width="1.5"/>

    <!-- Круговая антенна-платформа / хедфрейм на вершине -->
    <ellipse cx="160" cy="34" rx="20" ry="4" fill="#475569" stroke="#94a3b8" stroke-width="1"/>
    <!-- 3 секторные антенны на трубостойках монополя -->
    <rect x="142" y="20" width="4" height="16" rx="1" fill="#a78bfa"/>
    <rect x="158" y="18" width="4" height="16" rx="1" fill="#a78bfa"/>
    <rect x="174" y="20" width="4" height="16" rx="1" fill="#a78bfa"/>
    """


def _art_struct_pole() -> str:
    """Чертёж городской трубостойки / столба."""
    return """
    <line x1="100" y1="138" x2="220" y2="138" stroke="#475569" stroke-width="2"/>
    <rect x="153" y="134" width="14" height="6" fill="#334155"/>
    <rect x="157" y="38" width="6" height="96" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1"/>
    <!-- Монтажные выносные траверсы -->
    <line x1="146" y1="46" x2="174" y2="46" stroke="#64748b" stroke-width="2"/>
    <rect x="144" y="38" width="4" height="16" rx="1" fill="#fbbf24"/>
    <rect x="172" y="38" width="4" height="16" rx="1" fill="#fbbf24"/>
    """


def _art_struct_roof() -> str:
    """Чертёж крышной конструкции / трубостойки на парапете."""
    return """
    <!-- Крыша здания (парапет и плита) -->
    <rect x="80" y="110" width="160" height="28" fill="#1e293b" stroke="#334155" stroke-width="1.5"/>
    <rect x="76" y="106" width="40" height="10" fill="#334155"/>

    <!-- Балластная опора на кровле -->
    <rect x="135" y="104" width="50" height="6" fill="#475569"/>
    <!-- Подкосы / тренога -->
    <line x1="140" y1="104" x2="160" y2="60" stroke="#94a3b8" stroke-width="2"/>
    <line x1="180" y1="104" x2="160" y2="60" stroke="#94a3b8" stroke-width="2"/>
    <!-- Вертикальная стойка -->
    <rect x="158" y="28" width="4" height="80" fill="url(#metalChassis)" stroke="#94a3b8" stroke-width="1"/>
    <!-- Антенна на стойке -->
    <rect x="163" y="24" width="5" height="18" rx="1" fill="#f43f5e"/>
    """


def get_antenna_pixmap(antenna: Any, width: int = 300, height: int = 170, dark_theme: bool = True) -> Any:
    """Возвращает QPixmap иллюстрации антенны с кэшированием."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QImage, QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer

    if QApplication.instance() is None:
        return None

    ant_id = getattr(antenna, "id", str(antenna))
    key = (ant_id, width, height, dark_theme)
    if key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[key]

    svg_code = generate_antenna_svg(antenna, width=width, height=height, dark_theme=dark_theme)
    renderer = QSvgRenderer(QByteArray(svg_code.encode("utf-8")))
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    pix = QPixmap.fromImage(img)
    _PIXMAP_CACHE[key] = pix
    return pix


def get_antenna_icon(antenna: Any, size: int = 32, dark_theme: bool = True) -> Any:
    """Возвращает QIcon с миниатюрой антенны."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon

    if QApplication.instance() is None:
        return None

    ant_id = getattr(antenna, "id", str(antenna))
    key = (ant_id, size, dark_theme)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    pix = get_antenna_pixmap(antenna, width=size * 2, height=size * 2, dark_theme=dark_theme)
    if pix is not None and not pix.isNull():
        icon = QIcon(pix)
        _ICON_CACHE[key] = icon
        return icon
    return QIcon()


def get_structure_pixmap(spec: Any, width: int = 300, height: int = 170, dark_theme: bool = True) -> Any:
    """Возвращает QPixmap иллюстрации опорной конструкции."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtGui import QImage, QPainter, QPixmap
    from PyQt6.QtSvg import QSvgRenderer

    if QApplication.instance() is None:
        return None

    sid = getattr(spec, "id", str(spec))
    key = ("struct", sid, width, height, dark_theme)
    if key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[key]

    svg_code = generate_structure_svg(spec, width=width, height=height, dark_theme=dark_theme)
    renderer = QSvgRenderer(QByteArray(svg_code.encode("utf-8")))
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    pix = QPixmap.fromImage(img)
    _PIXMAP_CACHE[key] = pix
    return pix
