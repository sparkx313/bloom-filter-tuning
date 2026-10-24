"""标准布隆过滤器：只增不删的集合成员查询。

假阴性不可能出现（位只会从 0 变 1），假阳性按 `predicted_fp` 出现。这两个事实是全部
承诺，仓库里的基准就是在检查它们是否兑现。
"""

from .bits import BitArray
from .estimate import estimated_n, optimal_k, optimal_m, predicted_fp
from .hashing import POSITIONS


class Bloom:
    """`capacity` 个元素、目标误判率 `false_positive` 的位数组。

    直接给 `m` / `k` 可以跳过尺寸公式（基准测试需要成对的 m、k 才能画出一条曲线）。
    `scheme` 选位置生成方式，`salt` 让两个同尺寸过滤器互相独立。
    """

    def __init__(self, capacity, false_positive=0.01, m=None, k=None,
                 scheme="double", salt=b"bloom"):
        if m is None:
            m = optimal_m(capacity, false_positive)
        if k is None:
            k = optimal_k(m, capacity)
        if m < 8 or k < 1:
            raise ValueError("need m >= 8 and k >= 1, got m=%d k=%d" % (m, k))
        if scheme not in POSITIONS:
            raise ValueError("unknown scheme %r" % (scheme,))
        self.capacity = capacity
        self.target = false_positive
        self.m = m
        self.k = k
        self.scheme = scheme
        self.salt = salt
        self.bits = BitArray(m)
        self.inserted = 0
        self._positioner = POSITIONS[scheme]

    def positions(self, item):
        return self._positioner(item, self.m, self.k, self.salt)

    def add(self, item):
        for index in self.positions(item):
            self.bits.set(index)
        self.inserted += 1

    def update(self, items):
        for item in items:
            self.add(item)

    def __contains__(self, item):
        return all(self.bits.get(index) for index in self.positions(item))

    def bits_set(self):
        return self.bits.count()

    def load(self):
        """置位比例 —— 超过约 0.5 之后每多塞一点，误判率都会暴涨。"""
        return self.bits_set() / float(self.m)

    def predicted_fp(self):
        return predicted_fp(self.m, self.k, self.inserted)

    def estimated_inserted(self):
        """不看 `inserted` 计数器、只从位数组模式反推元素个数。"""
        return estimated_n(self.m, self.k, self.bits_set())

    def bytes_used(self):
        return self.bits.bytes_used()

    def reset(self):
        self.bits.clear()
        self.inserted = 0
