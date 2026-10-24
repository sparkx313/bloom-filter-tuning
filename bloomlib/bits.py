"""位数组与 4 bit 计数器数组。

布隆过滤器的全部状态就是一个位数组，所以这里只解决两件事：怎么把 m 个 bit 装进
`bytearray`（而不是 m 个 Python bool —— 那样每个槽位要 28 字节，内存数字就没法看了），
以及怎么在同样紧凑的表示里放得下可减小的计数器。
"""

MASKS = (0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01)


class BitArray:
    """m 个 bit，装在 `(m + 7) // 8` 个字节里。"""

    __slots__ = ("size", "data")

    def __init__(self, size, data=None):
        if size <= 0:
            raise ValueError("a bit array needs at least one bit")
        self.size = size
        self.data = bytearray((size + 7) // 8) if data is None else bytearray(data)
        if len(self.data) != (size + 7) // 8:
            raise ValueError("%d bytes cannot hold %d bits" % (len(self.data), size))

    def __len__(self):
        return self.size

    def get(self, index):
        return bool(self.data[index >> 3] & MASKS[index & 7])

    def set(self, index):
        """置位并返回它是不是新置的（`Bloom.bits_set` 靠这个增量维护）。"""
        mask = MASKS[index & 7]
        byte = self.data[index >> 3]
        if byte & mask:
            return False
        self.data[index >> 3] = byte | mask
        return True

    def bytes_used(self):
        return len(self.data)

    def count(self):
        """置位的 bit 数 —— 第 6 节用它反推已经塞进去多少个元素。"""
        total = 0
        for byte in self.data:
            total += POPCOUNT[byte]
        return total

    def clear(self):
        for index in range(len(self.data)):
            self.data[index] = 0


POPCOUNT = tuple(bin(value).count("1") for value in range(256))


class CounterArray:
    """m 个 0-15 的计数器，两个装一个字节。

    上限 15 是刻意的：`add` 加到 15 就饱和、`remove` 减到 0 就停，所以永远不会回绕
    （回绕会让一个还在集合里的元素被判定为不存在 —— 布隆过滤器唯一不能犯的错误）。
    代价是高频元素删不干净，基准测试量了这个代价。
    """

    __slots__ = ("size", "data")

    def __init__(self, size, width=4):
        if width != 4:
            raise ValueError("only 4-bit counters are packed here")
        self.size = size
        self.data = bytearray((size + 1) // 2)

    def get(self, index):
        nibble = self.data[index >> 1]
        return (nibble >> 4) if (index & 1) else (nibble & 0x0F)

    def _put(self, index, value):
        high = bool(index & 1)
        byte = self.data[index >> 1]
        if high:
            self.data[index >> 1] = (byte & 0x0F) | (value << 4)
        else:
            self.data[index >> 1] = (byte & 0xF0) | value

    def increment(self, index):
        """加一并返回是否发生了饱和（没加上去）。"""
        current = self.get(index)
        if current == 15:
            return True
        self._put(index, current + 1)
        return False

    def decrement(self, index):
        current = self.get(index)
        if current == 0:
            return False
        self._put(index, current - 1)
        return True

    def bytes_used(self):
        return len(self.data)
