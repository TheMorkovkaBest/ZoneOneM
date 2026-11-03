"""Обучение нейросети Алгоритма 2 (только numpy).

Этап 1 — обучение с учителем на датасете из tools/make_dataset.py:
    python tools/train_ai.py --data models/dataset.npz --epochs 30

Этап 2 (по желанию) — дообучение с подкреплением (REINFORCE): сеть сама
расставляет вышки на случайных участках и получает награду за итоговое покрытие:
    python tools/train_ai.py --data models/dataset.npz --rl-episodes 300 --init models/ai_surrogate.npz --epochs 0

Результат: models/ai_surrogate.npz — его подхватывает «Алгоритм 2» в программе.
"""
import argparse
import os
import random
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# все папки проекта с .py-файлами — в sys.path (модули проекта импортируются «плоско»: import coverage)
_SKIP = {".venv", "venv", "env", ".git", ".idea", "__pycache__", "node_modules", "build", "dist", "site-packages"}
for _dir, _subs, _files in os.walk(_ROOT):
    _subs[:] = [s for s in _subs if s not in _SKIP and not s.startswith(".")]
    if _dir == _ROOT or any(f.endswith(".py") for f in _files):
        if _dir not in sys.path:
            sys.path.insert(0, _dir)

import numpy as np                                   # noqa: E402

import ai_placement as ai                            # noqa: E402
from ai_model import LABEL_SCALE, Adam, SurrogateNet  # noqa: E402

RANK_WEIGHT = 0.1           # вес ранжирующей части потерь
RANK_PAIRS = 64             # пар клеток на пример


# ---------------------------------------------------------------------------
#  Данные
# ---------------------------------------------------------------------------
def augment(X, Y, M, rnd):
    """Поворот на 0/90/180/270° и отражение: тот же участок, новый пример бесплатно."""
    k = rnd.randrange(4)
    X, Y, M = np.rot90(X, k, axes=(-2, -1)), np.rot90(Y, k, axes=(-2, -1)), np.rot90(M, k, axes=(-2, -1))
    if rnd.random() < 0.5:
        X, Y, M = X[..., ::-1], Y[..., ::-1], M[..., ::-1]
    return np.ascontiguousarray(X), np.ascontiguousarray(Y), np.ascontiguousarray(M)


def batches(idx, X, Y, M, bs, rnd, aug=True):
    idx = list(idx)
    rnd.shuffle(idx)
    for s in range(0, len(idx), bs):
        b = idx[s:s + bs]
        xb, yb, mb = X[b].astype(np.float32), Y[b] * LABEL_SCALE, M[b].astype(np.float32)
        if aug:
            out = [augment(xb[i], yb[i], mb[i], rnd) for i in range(len(b))]
            xb, yb, mb = (np.stack(t) for t in zip(*out))
        yield xb, yb, mb


# ---------------------------------------------------------------------------
#  Потери
# ---------------------------------------------------------------------------
def loss_and_grad(P, Y, M, rnd):
    """Маскированная MSE + логистическая ранжирующая потеря по парам клеток.

    Ошибка считается только там, где метка известна (MASK = 1): остальные клетки
    не размечены и в потери не входят.
    """
    n = max(float(M.sum()), 1.0)
    diff = (P - Y) * M
    loss = float((diff ** 2).sum() / n)
    d_mse = 2.0 * diff / n
    d_rank = np.zeros(P.shape, dtype=np.float64)
    rank_loss, pairs = 0.0, 0
    for i in range(P.shape[0]):
        cells = np.flatnonzero(M[i].ravel())
        if len(cells) < 2:
            continue
        a = np.array([rnd.choice(cells) for _ in range(RANK_PAIRS)])
        b = np.array([rnd.choice(cells) for _ in range(RANK_PAIRS)])
        y, p = Y[i].ravel(), P[i].ravel()
        keep = y[a] > y[b] + 1e-4             # пара (a, b): в a прирост больше — сеть должна это знать
        a, b = a[keep], b[keep]
        if len(a) == 0:
            continue
        z = p[a] - p[b]
        rank_loss += float(np.logaddexp(0.0, -z).sum())
        g = -1.0 / (1.0 + np.exp(z))          # d/dz log(1 + e^−z)
        gi = d_rank[i].reshape(-1)
        np.add.at(gi, a, g)
        np.add.at(gi, b, -g)
        pairs += len(a)
    if pairs:
        loss += RANK_WEIGHT * rank_loss / pairs
        d_mse = d_mse + (RANK_WEIGHT / pairs) * d_rank
    dP = d_mse.astype(np.float32)
    return loss, dP


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(net, X, Y, M, idx, k=ai.TOP_K):
    """Метрики на проверочных участках: MSE, Спирмен, попадание лучшей клетки в top-K сети."""
    mse, rho, hit = [], [], []
    for s in range(0, len(idx), 32):
        b = idx[s:s + 32]
        P = net.forward(X[b].astype(np.float32)) / LABEL_SCALE
        for i, j in enumerate(b):
            cells = np.flatnonzero(M[j].ravel())
            y, p = Y[j].ravel()[cells], P[i].ravel()[cells]
            mse.append(float(((y - p) ** 2).mean()))
            rho.append(spearman(y, p))
            hit.append(int(np.argmax(y) in np.argsort(-p)[:k]))
    return float(np.mean(mse)), float(np.mean(rho)), float(np.mean(hit))


# ---------------------------------------------------------------------------
#  Этап 1: обучение с учителем
# ---------------------------------------------------------------------------
def train_supervised(net, data, epochs, bs, lr, out, rnd):
    X, Y, M = data["X"], data["Y"], data["MASK"]
    n = len(X)
    order = list(range(n))
    rnd.shuffle(order)
    n_val = max(1, n // 10)
    val, tr = order[:n_val], order[n_val:]
    print(f"Примеров: {n} (обучение {len(tr)}, проверка {len(val)})")
    opt = Adam(net.params, lr=lr)
    best = -1e9
    mse, rho, hit = evaluate(net, X, Y, M, val)
    print(f"  до обучения: MSE {mse:.5f}, Спирмен {rho:.3f}, top-{ai.TOP_K} {hit:.0%}")
    for ep in range(1, epochs + 1):
        t0, losses = time.perf_counter(), []
        if ep == int(epochs * 0.7) + 1:
            opt.lr *= 0.3                                    # под конец — шаг помельче
        for xb, yb, mb in batches(tr, X, Y, M, bs, rnd):
            P = net.forward(xb, keep=True)
            loss, dP = loss_and_grad(P, yb, mb, rnd)
            opt.step(net.params, net.backward(dP))
            losses.append(loss)
        mse, rho, hit = evaluate(net, X, Y, M, val)
        mark = ""
        if rho + hit > best:
            best = rho + hit
            net.save(out, {"val_spearman": rho, "val_topk": hit})
            mark = "  ← сохранено"
        print(f"  эпоха {ep}/{epochs}: loss {np.mean(losses):.4f}, проверка: MSE {mse:.5f}, "
              f"Спирмен {rho:.3f}, top-{ai.TOP_K} {hit:.0%}, {time.perf_counter() - t0:.0f} с{mark}", flush=True)
    return SurrogateNet.load(out) if os.path.exists(out) else net


# ---------------------------------------------------------------------------
#  Этап 2: дообучение с подкреплением (REINFORCE)
# ---------------------------------------------------------------------------
def run_episode(net, task, temp, rnd):
    """Сеть сама расставляет вышки, выбирая клетки случайно по softmax(карта / temp)."""
    ev = ai.CoverageEvaluator(task)
    grid = ai.Grid(task.area)
    static = ai.static_features(task, grid)
    allowed = static[1]
    if ev.n_points == 0 or not allowed.any():
        return None
    target = float(task.target_coverage_ratio)
    placed, states, actions, masks = [], [], [], []
    while len(placed) < task.max_towers and ev.ratio() < target:
        X, _, _ = ai.build_features(task, placed, grid, static)
        logits = net.forward(X[None])[0].astype(np.float64) / temp
        logits[~allowed] = -np.inf
        p = np.exp(logits - logits.max())
        p /= p.sum()
        a = int(rnd.choices(range(p.size), weights=p.ravel())[0])
        r, c = divmod(a, grid.n)
        lat, lon = float(grid.lats[r, c]), float(grid.lons[r, c])
        best = max((ev.try_tower(ai.make_tower(task, lat, lon, az)) + (az,) for az in ai.azimuths_for(task)),
                   key=lambda t: t[0])
        ev.add(best[1])
        placed.append(ai.make_tower(task, lat, lon, best[2]))
        states.append(X); actions.append(a); masks.append(allowed)
    # награда: итоговое покрытие минус штраф за число вышек
    reward = ev.ratio() - 0.05 * len(placed) / max(task.max_towers, 1)
    return reward, states, actions, masks


def train_rl(net, episodes, lr, temp, out, rnd, data=None, bs=8):
    import make_dataset as md
    opt = Adam(net.params, lr=lr)
    sup = None
    if data is not None:                     # параллельно немного учимся с учителем — чтобы не «забыть» карту
        idx = list(range(len(data["X"])))
        sup = (data["X"], data["Y"], data["MASK"], idx)
    baseline, rewards = None, []
    for ep in range(1, episodes + 1):
        task = md.random_task(rnd)
        res = run_episode(net, task, temp, rnd)
        if res is None:
            continue
        R, states, actions, masks = res
        baseline = R if baseline is None else 0.9 * baseline + 0.1 * R
        adv = R - baseline
        rewards.append(R)
        Xb = np.stack(states).astype(np.float32)
        logits = net.forward(Xb, keep=True).astype(np.float64) / temp
        dP = np.zeros(logits.shape, dtype=np.float32)
        for i, (a, m) in enumerate(zip(actions, masks)):
            lg = np.where(m, logits[i], -np.inf)
            p = np.exp(lg - lg.max()); p /= p.sum()
            g = p.copy(); g.ravel()[a] -= 1.0                  # ∂(−log π(a)) / ∂logits
            dP[i] = (adv * g / temp / len(actions)).astype(np.float32)
        grads = net.backward(dP)
        if sup is not None:
            X, Y, M, idx = sup
            b = rnd.sample(idx, min(bs, len(idx)))
            xb, yb, mb = next(batches(b, X, Y, M, bs, rnd))
            P = net.forward(xb, keep=True)
            _, dS = loss_and_grad(P, yb, mb, rnd)
            for k, v in net.backward(dS).items():
                grads[k] = grads[k] + v
        opt.step(net.params, grads)
        if ep % 10 == 0 or ep == episodes:
            print(f"  эпизод {ep}/{episodes}: средняя награда за последние 10 = {np.mean(rewards[-10:]):.3f}",
                  flush=True)
            net.save(out, {"rl_episodes": ep})
    return net


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(_ROOT, "models", "dataset.npz"))
    ap.add_argument("--out", default=ai.MODEL_PATH)
    ap.add_argument("--init", default=None, help="начать с уже обученных весов (.npz)")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--width", type=int, default=24, help="число фильтров в слое")
    ap.add_argument("--rl-episodes", type=int, default=0, help="эпизодов дообучения с подкреплением (0 — пропустить)")
    ap.add_argument("--rl-lr", type=float, default=1e-4)
    ap.add_argument("--rl-temp", type=float, default=0.3, help="температура выбора клетки при RL")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    rnd = random.Random(a.seed)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    net = SurrogateNet.load(a.init) if a.init else SurrogateNet(ai.N_CHANNELS, a.width, seed=a.seed)
    data = None
    if os.path.exists(a.data):
        d = np.load(a.data)
        data = {"X": d["X"], "Y": d["Y"], "MASK": d["MASK"]}
    elif a.epochs > 0:
        sys.exit(f"Нет датасета {a.data}. Сначала: python tools/make_dataset.py")

    if a.epochs > 0:
        print("Этап 1: обучение с учителем")
        net = train_supervised(net, data, a.epochs, a.batch, a.lr, a.out, rnd)
    if a.rl_episodes > 0:
        print("Этап 2: дообучение с подкреплением (REINFORCE)")
        net = train_rl(net, a.rl_episodes, a.rl_lr, a.rl_temp, a.out, rnd, data)
    if a.epochs == 0 and a.rl_episodes == 0:
        print("Нечего делать: задайте --epochs и/или --rl-episodes")
        return
    print(f"Модель сохранена: {a.out}")


if __name__ == "__main__":
    main()
