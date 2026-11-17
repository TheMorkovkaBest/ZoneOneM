"""Алгоритм 3: генетический алгоритм расстановки вышек.

Особь — набор вышек (ген = точка-кандидат + азимут антенны). Популяция особей
эволюционирует: турнирный отбор, скрещивание «разрезом карты» по случайной прямой,
мутации (сдвиг вышки, поворот антенны, добавление вышки в непокрытую зону,
удаление самой бесполезной вышки), элитизм. Лучшая особь в конце дорабатывается
локальным поиском.

Приспособленность (чем больше, тем лучше):

    F = W_COVER·покрытие + W_MARGIN·запас_сигнала − W_COST·стоимость − W_OVERLAP·перекрытие

  * покрытие — доля точек участка с уровнем не ниже min_quality_level (как в
    coverage.coverage_stats); всё, что выше target_coverage_ratio, засчитывается
    с весом OVER_TARGET_WEIGHT, поэтому после цели выгоднее убирать вышки, чем добавлять;
  * запас сигнала — насколько в среднем уровень выше требуемого (0..1);
  * стоимость — число площадок + SECTOR_COST за каждый дополнительный сектор
    на той же площадке, делённое на max_towers;
  * перекрытие — доля точек, которые покрывают две вышки и больше.

Чтобы алгоритм был быстрым, положения вышек берутся из конечного набора
кандидатов (крыши зданий, площадки Алгоритма 1 и равномерная сетка «опор»), а
уровни сигнала каждой вышки (кандидат × азимут) считаются один раз и кешируются.
Поэтому точная приспособленность особи — это лишь np.max по нескольким массивам.

Интерфейс — как у Алгоритма 2: ga_place(task) возвращает список словарей
{lat, lon, height, azimuth, beamwidth, building_id, site_name, gain_tiles, quality_level}.
"""
import math
import random
import time
from dataclasses import dataclass

import numpy as np

import coverage
import geo
from ai_placement import CoverageEvaluator, ROOF_MIN_H, azimuths_for, make_tower
from calculations import M_PER_DEG_LAT

# веса целевой функции
W_COVER = 1.0
W_MARGIN = 0.05
W_COST = 0.08
W_OVERLAP = 0.03
OVER_TARGET_WEIGHT = 0.25     # вес покрытия сверх target_coverage_ratio
TARGET_BONUS = 0.1            # премия за достижение цели (дороже любой одной вышки)
SECTOR_COST = 0.5             # доп. сектор на той же площадке дешевле новой площадки


@dataclass
class GAParams:
    population: int = 60
    generations: int = 200
    patience: int = 20            # остановка, если столько поколений нет улучшения
    tournament_k: int = 3
    elite: int = 4
    p_crossover: float = 0.8
    p_mutation: float = 0.2       # вероятность мутации гена (особи — см. mutate)
    move_radius_m: float = 150.0  # «сдвиг вышки ±d»
    seed_algorithm_1: bool = True  # добавить в начальную популяцию результат Алгоритма 1
    seed_algorithm_2: bool = False  # … и Алгоритма 2 (если есть обученная модель)
    use_surrogate: bool = True    # нейросеть Алгоритма 2 задаёт веса выбора кандидатов
    local_search: bool = True
    time_limit_s: float = 120.0   # страховка для GUI: эволюция прерывается по времени
    max_pool: int = 1500          # максимум точек-кандидатов
    seed: int = None              # зерно генератора (для воспроизводимости)


# ---------------------------------------------------------------------------
#  Точки-кандидаты
# ---------------------------------------------------------------------------
class CandidatePool:
    """Конечный набор допустимых точек установки и их соседства."""

    def __init__(self, task, extra_points=(), max_pool=1500, rnd=None):
        rnd = rnd or random.Random(0)
        lat_min, lat_max, lon_min, lon_max = geo.polygon_bounds(task.area)
        self.lat0 = (lat_min + lat_max) / 2.0
        self.lon0 = (lon_min + lon_max) / 2.0
        self.m_lon = M_PER_DEG_LAT * math.cos(math.radians(self.lat0))
        pts = []                   # (lat, lon, building_id, вес)
        # 1) крыши зданий внутри участка
        import algorithm_1
        for b in getattr(task.building_data, "buildings", []) or []:
            h = float(b.get("height", 0.0) or 0.0)
            poly = b.get("poly") or []
            if h < ROOF_MIN_H or len(poly) < 3:
                continue
            lat, lon = algorithm_1._building_roof_point(poly)
            if task.is_allowed(lat, lon):
                pts.append((lat, lon, b.get("id"), 1.0 + h / 10.0))
        if len(pts) > max_pool * 2 // 3:      # оставляем самые высокие
            pts.sort(key=lambda p: -p[3])
            pts = pts[:max_pool * 2 // 3]
        # 2) равномерная сетка опор на земле
        area = max(geo.polygon_area_m2(task.area), 1.0)
        n_grid = max(16, max_pool - len(pts) - len(extra_points))
        step = max(30.0, math.sqrt(area / n_grid))
        dlat, dlon = step / M_PER_DEG_LAT, step / self.m_lon
        for la in np.arange(lat_min + dlat / 2, lat_max, dlat):
            for lo in np.arange(lon_min + dlon / 2, lon_max, dlon):
                if task.is_allowed(float(la), float(lo)):
                    pts.append((float(la), float(lo), None, 1.0))
        # 3) площадки Алгоритма 1 / результаты других алгоритмов
        for la, lo, bid in extra_points:
            if task.is_allowed(la, lo):
                pts.append((float(la), float(lo), bid, 2.0))
        if not pts:                # крошечный участок: хотя бы вершины-центроид
            c = (sum(p[0] for p in task.area) / len(task.area), sum(p[1] for p in task.area) / len(task.area))
            if task.is_allowed(*c):
                pts.append((c[0], c[1], None, 1.0))
        self.lats = np.array([p[0] for p in pts], dtype=float)
        self.lons = np.array([p[1] for p in pts], dtype=float)
        self.bids = [p[2] for p in pts]
        self.weight = np.array([p[3] for p in pts], dtype=float)
        self.x = (self.lons - self.lon0) * self.m_lon
        self.y = (self.lats - self.lat0) * M_PER_DEG_LAT

    def __len__(self):
        return int(self.lats.size)

    def index_of(self, lat, lon):
        """Ближайший кандидат к точке."""
        x = (lon - self.lon0) * self.m_lon
        y = (lat - self.lat0) * M_PER_DEG_LAT
        return int(np.argmin((self.x - x) ** 2 + (self.y - y) ** 2))

    def near(self, i, radius_m):
        d2 = (self.x - self.x[i]) ** 2 + (self.y - self.y[i]) ** 2
        idx = np.flatnonzero(d2 <= radius_m * radius_m)
        return idx[idx != i]

    def nearest(self, i, k):
        d2 = (self.x - self.x[i]) ** 2 + (self.y - self.y[i]) ** 2
        return np.argsort(d2)[1:k + 1]


# ---------------------------------------------------------------------------
#  Оценка особей
# ---------------------------------------------------------------------------
class Fitness:
    """Кеш уровней сигнала по генам и расчёт приспособленности особи."""

    def __init__(self, task, pool, evaluator, azimuths):
        self.task, self.pool, self.ev, self.az = task, pool, evaluator, azimuths
        self.req = evaluator.req
        self.n = evaluator.n_points
        self.target = float(getattr(task, "target_coverage_ratio", 0.95))
        self.max_towers = max(1, int(task.max_towers))
        self._levels = {}
        self._fit = {}
        self.towers_built = 0

    def levels(self, gene):
        lv = self._levels.get(gene)
        if lv is None:
            i, a = gene
            t = make_tower(self.task, float(self.pool.lats[i]), float(self.pool.lons[i]), self.az[a])
            lv = np.asarray(coverage.level_at_points(t, self.ev.lats, self.ev.lons), dtype=np.int8)
            self._levels[gene] = lv
            self.towers_built += 1
        return lv

    def details(self, genes):
        """(F, покрытие, запас, стоимость, перекрытие) для набора генов."""
        if not genes:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        L = np.stack([self.levels(g) for g in genes])
        best = L.max(axis=0)
        ok = best >= self.req
        cov = float(ok.mean())
        margin = float(np.clip(best - self.req, 0, None).mean()) / max(1, 4 - self.req)
        overlap = float(((L >= self.req).sum(axis=0) >= 2).mean())
        n_sites = len({g[0] for g in genes})
        cost = (n_sites + SECTOR_COST * (len(genes) - n_sites)) / self.max_towers
        if cov < self.target:
            cov_eff = cov
        else:
            cov_eff = self.target + TARGET_BONUS + OVER_TARGET_WEIGHT * (cov - self.target)
        f = W_COVER * cov_eff + W_MARGIN * margin - W_COST * cost - W_OVERLAP * overlap
        return f, cov, margin, cost, overlap

    def __call__(self, genes):
        key = tuple(sorted(genes))
        f = self._fit.get(key)
        if f is None:
            f = self.details(genes)[0]
            self._fit[key] = f
        return f

    def coverage(self, genes):
        if not genes:
            return 0.0
        return float((np.max([self.levels(g) for g in genes], axis=0) >= self.req).mean())

    def contributions(self, genes):
        """Сколько точек потеряется без каждой вышки (уникальный вклад)."""
        if len(genes) == 1:
            return [int((self.levels(genes[0]) >= self.req).sum())]
        ok = np.stack([self.levels(g) >= self.req for g in genes])
        cnt = ok.sum(axis=0)
        return [int((ok[k] & (cnt == 1)).sum()) for k in range(len(genes))]


# ---------------------------------------------------------------------------
#  Генетический алгоритм
# ---------------------------------------------------------------------------
class GeneticPlacer:
    def __init__(self, task, params=None):
        self.task = task
        self.p = params or GAParams()
        self.rnd = random.Random(self.p.seed)
        self.az = azimuths_for(task)
        self.max_towers = max(1, int(task.max_towers))
        self.max_per_site = max(1, int(getattr(task, "max_sectors_per_site", 3)))
        if len(self.az) == 1:
            self.max_per_site = 1          # круговая антенна: второй сектор на крыше бесполезен
        self.progress = getattr(task, "progress_callback", None)
        self.cancelled = getattr(task, "is_cancelled", None)
        self.history = []                  # лучшая F по поколениям (для отчёта)

    # --- служебное -------------------------------------------------------
    def _is_cancelled(self):
        return bool(self.cancelled and self.cancelled())

    def _report(self, pct, msg, eta=None):
        if self.progress:
            self.progress(min(99.0, max(0.0, pct)), msg, eta)

    def _az_index(self, az):
        return int(np.argmin([abs((az - a + 180.0) % 360.0 - 180.0) for a in self.az]))

    def repair(self, genes):
        """Без повторов, не больше max_per_site секторов на площадке и max_towers вышек."""
        out, per_site = [], {}
        for g in dict.fromkeys(genes):     # убираем дубли, порядок сохраняется
            if per_site.get(g[0], 0) >= self.max_per_site:
                continue
            per_site[g[0]] = per_site.get(g[0], 0) + 1
            out.append(g)
        while len(out) > self.max_towers:
            contrib = self.fit.contributions(out)
            out.pop(int(np.argmin(contrib)))
        return out

    def random_gene(self, prior=None):
        w = self.weight if prior is None else prior
        i = int(self.np_rnd.choice(len(self.pool), p=w))
        return (i, self.rnd.randrange(len(self.az)))

    # --- начальная популяция ----------------------------------------------
    def random_individual(self, k_hint):
        k = max(1, min(self.max_towers, int(round(self.rnd.gauss(k_hint, max(1.0, k_hint * 0.4))))))
        genes = []
        for _ in range(k * 3):             # разносим вышки: не ближе 1/2 радиуса сдвига
            if len(genes) >= k:
                break
            g = self.random_gene()
            if all((self.pool.x[g[0]] - self.pool.x[h[0]]) ** 2 + (self.pool.y[g[0]] - self.pool.y[h[0]]) ** 2
                   >= (self.p.move_radius_m * 0.5) ** 2 for h in genes):
                genes.append(g)
        return self.repair(genes)

    def genes_from_points(self, points):
        genes = []
        for p in points or []:
            i = self.pool.index_of(p["lat"], p["lon"])
            genes.append((i, self._az_index(float(p.get("azimuth", 0.0)))))
        return self.repair(genes)

    # --- операторы --------------------------------------------------------
    def tournament(self, pop, fits):
        best = None
        for _ in range(self.p.tournament_k):
            j = self.rnd.randrange(len(pop))
            if best is None or fits[j] > fits[best]:
                best = j
        return pop[best]

    def crossover(self, a, b):
        """Разрез карты случайной прямой: с одной стороны вышки родителя A, с другой — B."""
        k = self.rnd.randrange(len(self.pool))
        ang = self.rnd.uniform(0.0, math.pi)
        nx, ny = math.cos(ang), math.sin(ang)
        x0, y0 = self.pool.x[k], self.pool.y[k]

        def side(g):
            return (self.pool.x[g[0]] - x0) * nx + (self.pool.y[g[0]] - y0) * ny >= 0.0

        c1 = [g for g in a if side(g)] + [g for g in b if not side(g)]
        c2 = [g for g in b if side(g)] + [g for g in a if not side(g)]
        return self.repair(c1) or list(a), self.repair(c2) or list(b)

    def mutate(self, genes):
        genes = list(genes)
        # точечные мутации генов: сдвиг ±d и поворот антенны
        for k in range(len(genes)):
            if self.rnd.random() < self.p.p_mutation:
                i, a = genes[k]
                if len(self.az) > 1 and self.rnd.random() < 0.4:
                    a = (a + self.rnd.choice([-1, 1])) % len(self.az)
                else:
                    near = self.pool.near(i, self.p.move_radius_m)
                    if near.size:
                        i = int(near[self.rnd.randrange(near.size)])
                genes[k] = (i, a)
        # структурные мутации особи
        if self.rnd.random() < self.p.p_mutation:
            genes = self.mutate_add(genes)
        if len(genes) > 1 and self.rnd.random() < self.p.p_mutation:
            contrib = self.fit.contributions(genes)
            genes.pop(int(np.argmin(contrib)))
        return self.repair(genes)

    def mutate_add(self, genes):
        """Новая вышка рядом со случайной непокрытой точкой участка."""
        if len(genes) >= self.max_towers:
            return genes
        if genes:
            best = np.max([self.fit.levels(g) for g in genes], axis=0)
            holes = np.flatnonzero(best < self.fit.req)
        else:
            holes = np.arange(self.fit.n)
        if holes.size == 0:
            return genes
        h = int(holes[self.rnd.randrange(holes.size)])
        j = self.pool.index_of(float(self.ev.lats[h]), float(self.ev.lons[h]))
        cands = np.concatenate([[j], self.pool.near(j, self.p.move_radius_m)])
        w = self.weight[cands]
        i = int(cands[self.np_rnd.choice(cands.size, p=w / w.sum())])
        if len(self.az) > 1:               # антенна — в сторону дыры
            dx = (float(self.ev.lons[h]) - self.pool.lons[i]) * self.pool.m_lon
            dy = (float(self.ev.lats[h]) - self.pool.lats[i]) * M_PER_DEG_LAT
            a = self._az_index(math.degrees(math.atan2(dx, dy)) % 360.0) if dx or dy else self.rnd.randrange(len(self.az))
        else:
            a = 0
        return genes + [(i, a)]

    # --- локальный поиск --------------------------------------------------
    def local_search(self, genes, deadline):
        """Первое улучшение: убрать вышку / сдвинуть к соседнему кандидату / повернуть."""
        best_f = self.fit(genes)
        improved = True
        while improved and time.perf_counter() < deadline and not self._is_cancelled():
            improved = False
            for k in range(len(genes)):
                i, a = genes[k]
                moves = [genes[:k] + genes[k + 1:]] if len(genes) > 1 else []
                moves += [genes[:k] + [(i, b)] + genes[k + 1:] for b in range(len(self.az)) if b != a]
                moves += [genes[:k] + [(int(j), a)] + genes[k + 1:] for j in self.pool.nearest(i, 8)]
                for m in moves:
                    m = self.repair(m)
                    f = self.fit(m)
                    if f > best_f + 1e-9:
                        genes, best_f, improved = m, f, True
                        break
                if improved or time.perf_counter() >= deadline:
                    break
        return genes

    # --- главный цикл -----------------------------------------------------
    def run(self):
        task, p = self.task, self.p
        if not task.area or len(task.area) < 3:
            return []
        t_start = time.perf_counter()
        deadline = t_start + p.time_limit_s
        self.np_rnd = np.random.default_rng(p.seed)
        self._report(0.0, "ГА: подготовка кандидатов")
        self.ev = CoverageEvaluator(task)
        if self.ev.n_points == 0:
            return []

        seeds = []
        if p.seed_algorithm_1:
            import algorithm_1
            self._report(1.0, "ГА: затравка — Алгоритм 1")
            sub = _quiet_copy(task)
            seeds.append(algorithm_1.optimize_coverage(sub) or [])
        if p.seed_algorithm_2:
            import ai_placement
            model = ai_placement.load_model()
            if model is not None:
                self._report(3.0, "ГА: затравка — Алгоритм 2")
                seeds.append(ai_placement.ai_place(_quiet_copy(task), model) or [])
        if self._is_cancelled():
            return []
        extra = [(s["lat"], s["lon"], s.get("building_id")) for r in seeds for s in r]
        self.pool = CandidatePool(task, extra, p.max_pool, self.rnd)
        if len(self.pool) == 0:
            return []
        self.weight = self._prior(task)
        self.fit = Fitness(task, self.pool, self.ev, self.az)

        # оценка нужного числа вышек: по затравкам или по площади одной вышки
        hint = np.mean([len(s) for s in seeds if s]) if any(seeds) else self._estimate_towers()
        pop = [self.genes_from_points(s) for s in seeds if s]
        while len(pop) < p.population:
            pop.append(self.random_individual(hint))
            if self._is_cancelled():
                return []
            if len(pop) % 10 == 0:
                self._report(5.0 + 10.0 * len(pop) / p.population, "ГА: начальная популяция")

        fits = [self.fit(g) for g in pop]
        best_f = max(fits)
        best = pop[int(np.argmax(fits))]
        stale = 0
        t_evo = time.perf_counter()
        gen = 0
        for gen in range(1, p.generations + 1):
            if self._is_cancelled():
                return []
            order = np.argsort(fits)[::-1]
            new = [pop[j] for j in order[:p.elite]]
            while len(new) < p.population:
                a, b = self.tournament(pop, fits), self.tournament(pop, fits)
                if self.rnd.random() < p.p_crossover:
                    a, b = self.crossover(a, b)
                new.append(self.mutate(a))
                if len(new) < p.population:
                    new.append(self.mutate(b))
            pop = [g if g else self.random_individual(hint) for g in new]
            fits = [self.fit(g) for g in pop]
            j = int(np.argmax(fits))
            if fits[j] > best_f + 1e-6:
                best_f, best, stale = fits[j], pop[j], 0
            else:
                stale += 1
            self.history.append(best_f)
            el = time.perf_counter() - t_evo
            eta = el / gen * min(p.generations - gen, p.patience - stale)
            self._report(15.0 + 75.0 * gen / p.generations,
                         f"ГА: поколение {gen}, покрытие {self.fit.coverage(best):.0%}, вышек {len(best)}", eta)
            if stale >= p.patience or time.perf_counter() > deadline:
                break
        self.generations_run = gen

        if p.local_search:
            self._report(92.0, "ГА: локальный поиск")
            best = self.local_search(best, max(deadline, time.perf_counter() + 5.0))
        if self._is_cancelled():
            return []
        return self.to_result(best)

    def _prior(self, task):
        """Веса выбора кандидатов: высота крыши; при наличии модели — ещё и оценка нейросети."""
        w = self.pool.weight.copy()
        if self.p.use_surrogate:
            try:
                import ai_placement as ai
                model = ai.load_model()
                if model is not None:
                    X, grid, _ = ai.build_features(task, [])
                    score = model.predict(X).astype(float)
                    cells = [grid.cell_of(la, lo) for la, lo in zip(self.pool.lats, self.pool.lons)]
                    s = np.array([score[r, c] for r, c in cells])
                    s = (s - s.min()) / (np.ptp(s) + 1e-9)
                    w = w * (0.25 + s)
            except Exception:
                pass                       # нейросеть — только подсказка, без неё ГА работает
        return w / w.sum()

    def _estimate_towers(self):
        """Грубая оценка: сколько вышек нужно, если каждая покрывает свою «среднюю» площадь."""
        sample = [self.random_gene() for _ in range(min(6, len(self.pool)))]
        per = np.mean([(self.fit.levels(g) >= self.fit.req).mean() for g in sample]) if sample else 0.0
        if per <= 0:
            return float(self.max_towers)
        return float(min(self.max_towers, max(1.0, self.fit.target / per)))

    def to_result(self, genes):
        """Вышки в порядке убывания вклада (как ставил бы жадный алгоритм) + отсев бесполезных."""
        n = self.fit.n
        min_gain = max(1, int(getattr(self.task, "min_gain_tiles", 3)))
        order, rest = [], list(genes)
        best = np.zeros(n, dtype=np.int8)
        cur = 0
        quality = getattr(self.task, "min_quality_level", "good")
        result = []
        while rest:
            gains = [int((np.maximum(best, self.fit.levels(g)) >= self.fit.req).sum()) - cur for g in rest]
            k = int(np.argmax(gains))
            if gains[k] < min_gain:
                break
            g = rest.pop(k)
            best = np.maximum(best, self.fit.levels(g))
            cur += gains[k]
            order.append(g)
            i, a = g
            t = make_tower(self.task, float(self.pool.lats[i]), float(self.pool.lons[i]), self.az[a])
            bid = self.pool.bids[i]
            result.append({
                "lat": t.lat, "lon": t.lon, "height": t.height, "azimuth": float(self.az[a]),
                "beamwidth": t.beamwidth, "building_id": bid,
                "site_name": f"ГА-площадка {len({o[0] for o in order})}",
                "gain_tiles": gains[k], "quality_level": quality,
            })
        self._report(99.0, f"ГА: готово, покрытие {cur / max(n, 1):.0%}, вышек {len(result)}")
        return result


def _quiet_copy(task):
    """Копия задачи без колбэков прогресса (затравочные алгоритмы не сбивают прогресс ГА)."""
    import copy
    sub = copy.copy(task)
    sub.progress_callback = None
    return sub


def ga_place(task, params=None):
    """Алгоритм 3: генетическая расстановка. Формат результата — как у ai_place."""
    return GeneticPlacer(task, params).run()
