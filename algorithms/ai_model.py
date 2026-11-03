"""Нейросеть-суррогат для Алгоритма 2 (только numpy, без PyTorch).

Сеть «картинка → картинка»: на входе растр признаков участка [C, H, W], на выходе
карта [H, W] — предсказанный прирост доли покрытия, если поставить вышку в клетку.

Архитектура — свёрточная сеть с расширенными (dilated) свёртками 3×3:
дилатации 1, 2, 4, 8, 16 дают поле зрения 63 клетки, то есть каждая клетка
«видит» почти весь участок. Затем свёртка 1×1 в один выходной канал.
Прямой и обратный проход написаны вручную, поэтому для работы программы и для
обучения нужен только numpy (никаких torch/onnxruntime).
"""
import numpy as np

DILATIONS = (1, 2, 4, 8, 16)
LABEL_SCALE = 10.0          # метки (доли 0..0.3) умножаются при обучении, чтобы градиенты были крупнее


def _im2col(x, d):
    """[N, C, H, W] → [N, C*9, H*W] для свёртки 3×3 с дилатацией d и паддингом d."""
    n, c, h, w = x.shape
    xp = np.pad(x, ((0, 0), (0, 0), (d, d), (d, d)))
    cols = np.empty((n, c, 9, h, w), dtype=x.dtype)
    k = 0
    for i in range(3):
        for j in range(3):
            cols[:, :, k] = xp[:, :, i * d:i * d + h, j * d:j * d + w]
            k += 1
    return cols.reshape(n, c * 9, h * w)


def _col2im(cols, shape, d):
    """Обратная операция к _im2col (суммирует перекрытия)."""
    n, c, h, w = shape
    cols = cols.reshape(n, c, 9, h, w)
    xp = np.zeros((n, c, h + 2 * d, w + 2 * d), dtype=cols.dtype)
    k = 0
    for i in range(3):
        for j in range(3):
            xp[:, :, i * d:i * d + h, j * d:j * d + w] += cols[:, :, k]
            k += 1
    return xp[:, :, d:d + h, d:d + w]


class SurrogateNet:
    """Свёрточная сеть с дилатациями; веса хранятся в словаре numpy-массивов."""

    def __init__(self, in_ch, width=24, seed=0):
        rng = np.random.default_rng(seed)
        self.in_ch, self.width = int(in_ch), int(width)
        self.params = {}
        c = self.in_ch
        for li, _ in enumerate(DILATIONS):
            fan_in = c * 9
            self.params[f"W{li}"] = (rng.standard_normal((width, fan_in)) * np.sqrt(2.0 / fan_in)).astype(np.float32)
            self.params[f"b{li}"] = np.zeros(width, dtype=np.float32)
            c = width
        self.params["Wo"] = (rng.standard_normal((1, width)) * np.sqrt(1.0 / width)).astype(np.float32)
        self.params["bo"] = np.zeros(1, dtype=np.float32)

    # --- прямой проход ---------------------------------------------------
    def forward(self, x, keep=False):
        """x: [N, C, H, W] → [N, H, W] (в масштабе LABEL_SCALE). keep=True сохраняет кэш для backward."""
        x = np.asarray(x, dtype=np.float32)
        n, _, h, w = x.shape
        cache = []
        a = x
        for li, d in enumerate(DILATIONS):
            cols = _im2col(a, d)
            z = np.einsum("oc,ncp->nop", self.params[f"W{li}"], cols, optimize=True)
            z += self.params[f"b{li}"][None, :, None]
            out = np.maximum(z, 0.0).reshape(n, self.width, h, w)
            if keep:
                cache.append((cols, a.shape, z > 0))
            a = out
        flat = a.reshape(n, self.width, h * w)
        y = np.einsum("oc,ncp->nop", self.params["Wo"], flat, optimize=True)[:, 0] + self.params["bo"][0]
        if keep:
            self._cache = (cache, flat, (n, h, w))
        return y.reshape(n, h, w)

    def predict(self, x):
        """Один растр [C, H, W] → карта прироста [H, W] в долях покрытия."""
        return self.forward(np.asarray(x)[None])[0] / LABEL_SCALE

    # --- обратный проход ---------------------------------------------------
    def backward(self, dy):
        """dy: [N, H, W] — градиент потерь по выходу. Возвращает словарь градиентов."""
        cache, flat, (n, h, w) = self._cache
        grads = {}
        dy = np.asarray(dy, dtype=np.float32).reshape(n, 1, h * w)
        grads["Wo"] = np.einsum("nop,ncp->oc", dy, flat, optimize=True)
        grads["bo"] = dy.sum(axis=(0, 2))
        da = np.einsum("oc,nop->ncp", self.params["Wo"], dy, optimize=True)      # [N, width, HW]
        for li in reversed(range(len(DILATIONS))):
            cols, in_shape, pos = cache[li]
            dz = da * pos
            grads[f"W{li}"] = np.einsum("nop,ncp->oc", dz, cols, optimize=True)
            grads[f"b{li}"] = dz.sum(axis=(0, 2))
            if li > 0:
                dcols = np.einsum("oc,nop->ncp", self.params[f"W{li}"], dz, optimize=True)
                da = _col2im(dcols, in_shape, DILATIONS[li]).reshape(n, in_shape[1], h * w)
        self._cache = None
        return grads

    # --- сохранение -------------------------------------------------------
    def save(self, path, meta=None):
        extra = {f"meta_{k}": np.asarray(v) for k, v in (meta or {}).items()}
        np.savez_compressed(path, in_ch=self.in_ch, width=self.width, **self.params, **extra)

    @classmethod
    def load(cls, path):
        data = np.load(path)
        net = cls(int(data["in_ch"]), int(data["width"]))
        for k in net.params:
            net.params[k] = data[k].astype(np.float32)
        net.meta = {k[5:]: data[k] for k in data.files if k.startswith("meta_")}
        return net


class Adam:
    def __init__(self, params, lr=1e-3, b1=0.9, b2=0.999, eps=1e-8):
        self.lr, self.b1, self.b2, self.eps, self.t = lr, b1, b2, eps, 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, params, grads, clip=5.0):
        self.t += 1
        norm = np.sqrt(sum(float((g * g).sum()) for g in grads.values()))
        scale = min(1.0, clip / (norm + 1e-12))           # обрезка градиента: защита от взрывов
        for k, g in grads.items():
            g = g * scale
            self.m[k] = self.b1 * self.m[k] + (1 - self.b1) * g
            self.v[k] = self.b2 * self.v[k] + (1 - self.b2) * g * g
            mh = self.m[k] / (1 - self.b1 ** self.t)
            vh = self.v[k] / (1 - self.b2 ** self.t)
            params[k] -= (self.lr * mh / (np.sqrt(vh) + self.eps)).astype(np.float32)
