"""Автоматическая расстановка вышек на выделенном участке.

Здесь будут жить три алгоритма. GUI уже умеет:
  * рисовать на карте участок (``task.area``) и запретные зоны (``task.forbidden``);
  * запускать выбранный алгоритм в фоновом потоке и ставить вышки в возвращённые точки;
  * показывать долю покрытия участка (coverage.coverage_stats).

Чтобы добавить алгоритм, достаточно написать функцию ``f(task) -> [(lat, lon), ...]``
и повесить на неё декоратор ``@register_algorithm("Название")``.
Подсказка для целевой функции: ``coverage.coverage_stats`` + ``tower.Tower`` —
расчёт одной вышки с учётом зданий занимает единицы миллисекунд.
"""
from dataclasses import dataclass, field

import geo

ALGORITHMS = {}


@dataclass
class PlacementTask:
    area: list                                   # полигон участка [(lat, lon), ...]
    forbidden: list = field(default_factory=list)   # запретные полигоны
    tower_type: dict = field(default_factory=dict)  # параметры вышки (freq, power, height, ...)
    max_towers: int = 10
    building_data: object = None                 # buildings.BuildingData или None
    target_coverage_ratio: float = 0.95          # целевая доля среднего покрытия (например, 0.95 для >= 95%)
    min_quality_level: str = "good"              # "good" (средняя зона и выше, >= -85 дБм), "weak", "excellent"
    grid_step_m: float = 20.0                    # шаг целевой сетки (м)
    azimuth_step_deg: float = 45.0               # шаг перебора азимута (градусы)
    min_gain_tiles: int = 3                      # минимальный полезный прирост точек
    max_sectors_per_site: int = 3                # максимум секторов на одну крышу / площадку
    progress_callback: object = None             # callback(pct, msg, eta_s)
    is_cancelled: object = None                  # callback() -> bool

    def is_allowed(self, lat, lon):
        """Точка внутри участка и вне запретных зон."""
        if not geo.point_in_polygon(lat, lon, self.area):
            return False
        return not any(geo.point_in_polygon(lat, lon, fz) for fz in self.forbidden)


def register_algorithm(name):
    def deco(func):
        ALGORITHMS[name] = func
        return func
    return deco


@register_algorithm("Алгоритм 1 (жадный с перебором азимутов)")
def algorithm_1(task):
    import algorithm_1 as algo1
    return algo1.optimize_coverage(task)


@register_algorithm("Алгоритм 2 (ИИ: нейросеть + проверка)")
def algorithm_2(task):
    import ai_placement
    model = ai_placement.load_model()
    if model is None:            # модель ещё не обучена (нет models/ai_surrogate.npz) — честный запасной путь
        if task.progress_callback:
            task.progress_callback(1.0, "ИИ-модель не найдена, работает Алгоритм 1", None)
        return algorithm_1(task)
    return ai_placement.ai_place(task, model)


@register_algorithm("Алгоритм 3 (генетический)")
def algorithm_3(task):
    import ga_placement
    return ga_placement.ga_place(task)


def auto_place_towers(area_bounds, tower_params):
    """Прежний интерфейс (оставлен для совместимости).

    :param area_bounds: dict (lat_min, lat_max, lon_min, lon_max)
    :param tower_params: dict (freq, power, height, tilt, wall_loss)
    :return: список координат [(lat, lon), ...]
    """
    # TODO: реализовать алгоритм автоматического размещения
    return []
