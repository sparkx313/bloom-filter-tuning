"""容量未知的流：一层装满就再开一层。

第 i 层的容量是上一层的 `growth` 倍、目标误判率是上一层的 1/`decay`，各层用不同的盐，
所以同一元素在不同层落到完全不同的位置上。查询代价是 `k * 层数` 个数位 —— 这是这个
方案唯一真正的代价，也是第 7 节量它的地方。
"""

from .bloom import Bloom
from .estimate import optimal_k, optimal_m


class DynamicBloom:
    """分层增长的布隆过滤器。

    `decay=2.0` 时第 i 层的目标是 `p / 2^(i+1)`，各层预算之和 `1/2 + 1/4 + ...` 收敛到
    `p`，联合上界不会超过最初的目标；`decay=1.0` 时每层用同一个目标，上界变成「层数 x 目标」，
    更省内存但更差 —— 两者都留在这里可以被量。
    """

    def __init__(self, capacity, false_positive=0.01, growth=2, decay=2.0,
                 scheme="double"):
        if growth < 2:
            raise ValueError("growth must be at least 2 or a layer never gains room")
        if decay < 1.0:
            raise ValueError("decay below 1 would make later layers sloppier than the first")
        self.capacity = capacity
        self.false_positive = false_positive
        self.growth = growth
        self.decay = decay
        self.scheme = scheme
        self.inserted = 0
        first = self._layer(0, None)
        self.k = first.k
        self.layers = [first]

    def _layer(self, index, k):
        capacity = int(round(self.capacity * (self.growth ** index)))
        target = self.false_positive / (self.decay ** (index + 1))
        m = optimal_m(capacity, target)
        return Bloom(capacity, target, m=m, k=k if k is not None else optimal_k(m, capacity),
                     scheme=self.scheme, salt=("layer-%d" % index).encode("ascii"))

    def add(self, item):
        layer = self.layers[-1]
        layer.add(item)
        self.inserted += 1
        if layer.inserted >= layer.capacity:
            self.layers.append(self._layer(len(self.layers), self.k))

    def update(self, items):
        for item in items:
            self.add(item)

    def __contains__(self, item):
        return any(item in layer for layer in self.layers)

    def probes(self):
        """一次查询要看多少个数位。"""
        return self.k * len(self.layers)

    def bytes_used(self):
        return sum(layer.bytes_used() for layer in self.layers)

    def total_capacity(self):
        return sum(layer.capacity for layer in self.layers)

    def unused_capacity(self):
        return 1.0 - self.inserted / float(self.total_capacity())

    def fp_bound(self):
        """各层独立命中的联合上界：`1 - product(1 - fp_i)`。"""
        miss = 1.0
        for layer in self.layers:
            miss *= 1.0 - layer.predicted_fp()
        return 1.0 - miss
