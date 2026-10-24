"""可删除的布隆过滤器：位置上放计数器而不是 bit。

`remove` 把 k 个计数器各减一，于是能删元素。代价是 4 bit 计数器的上限是 15：一个位置
被多次命中后再也加不上去，删除时却照样能减，于是「减掉的账可能记在别的元素头上」。
饱和次数在这里被计数，基准测试量了它带来多少假阴性。
"""

from .bits import CounterArray
from .estimate import optimal_k, optimal_m
from .hashing import POSITIONS


class CountingBloom:
    """支持 `remove` 的布隆过滤器，内存是标准版的 4 倍。"""

    def __init__(self, capacity, false_positive=0.01, m=None, k=None,
                 scheme="double", salt=b"counting"):
        if m is None:
            m = optimal_m(capacity, false_positive)
        if k is None:
            k = optimal_k(m, capacity)
        if scheme not in POSITIONS:
            raise ValueError("unknown scheme %r" % (scheme,))
        self.capacity = capacity
        self.target = false_positive
        self.m = m
        self.k = k
        self.scheme = scheme
        self.salt = salt
        self.counters = CounterArray(m)
        self.inserted = 0
        self.saturated = 0
        self._positioner = POSITIONS[scheme]

    def update(self, items):
        for item in items:
            self.add(item)

    def remove_all(self, items):
        """删除一批，返回真正被删掉的个数（位置已经是 0 的那些没动）。"""
        return sum(1 for item in items if self.remove(item))

    def positions(self, item):
        return self._positioner(item, self.m, self.k, self.salt)

    def add(self, item):
        for index in self.positions(item):
            if self.counters.increment(index):
                self.saturated += 1
        self.inserted += 1

    def remove(self, item):
        """减去这个元素的贡献。某个位置已经是 0 时什么都不做并返回 False。"""
        indices = self.positions(item)
        if any(self.counters.get(index) == 0 for index in indices):
            return False
        for index in indices:
            self.counters.decrement(index)
        self.inserted -= 1
        return True

    def __contains__(self, item):
        return all(self.counters.get(index) > 0 for index in self.positions(item))

    def predicted_fp(self):
        """按当前占用率算，不按 `inserted`：删除之后两者会分开。

        非零计数器比例就是位数组版本里 `1 - e^(-kn/m)` 那一项，所以同一个公式在这里
        是 `(X/m)^k`。用 `inserted` 会让删过元素的过滤器报出一个远低于实测的值。
        """
        return (self.counters_set() / float(self.m)) ** self.k

    def counters_set(self):
        """非零计数器的个数，和标准版的 `bits_set` 对应。按字节数而不是按计数器数。"""
        total = 0
        for byte in self.counters.data:
            # 奇数 m 时最后一个字节的高半字节没人用，也永远是 0，不用特判。
            total += (1 if byte & 0x0F else 0) + (1 if byte >> 4 else 0)
        return total

    def bytes_used(self):
        return self.counters.bytes_used()
