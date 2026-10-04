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


@register_algorithm("Алгоритм 1 (заглушка)")
def algorithm_1(task):
    raise NotImplementedError("Алгоритм 1 ещё не реализован (auto_placement.algorithm_1)")


@register_algorithm("Алгоритм 2 (заглушка)")
def algorithm_2(task):
    raise NotImplementedError("Алгоритм 2 ещё не реализован (auto_placement.algorithm_2)")


@register_algorithm("Алгоритм 3 (заглушка)")
def algorithm_3(task):
    raise NotImplementedError("Алгоритм 3 ещё не реализован (auto_placement.algorithm_3)")


def auto_place_towers(area_bounds, tower_params):
    """Прежний интерфейс (оставлен для совместимости).

    :param area_bounds: dict (lat_min, lat_max, lon_min, lon_max)
    :param tower_params: dict (freq, power, height, tilt, wall_loss)
    :return: список координат [(lat, lon), ...]
    """
    # TODO: реализовать алгоритм автоматического размещения
    return []
