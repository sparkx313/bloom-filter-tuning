"""把元素变成位置下标。

一个真随机oracle是不可能实现的，所以这里比较三种实际写法：一次哈希线性外推（Kirsch–
Mitzenmacher）、每个位置独立哈希、以及一次哈希加 splitmix64 展开。三者的理论假设不同、
每次查询的耗时不同，第 5 节量的是它们在实测误判率上有没有区别。
"""

import hashlib

MASK64 = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15


def as_bytes(item):
    if isinstance(item, bytes):
        return item
    if isinstance(item, str):
        return item.encode("utf-8")
    raise TypeError("hash filter items must be bytes or str, got %s" % type(item).__name__)


def digest64(item, salt=b""):
    """blake2b 截到 8 字节 —— 一个 64 位摘要，比 md5 快且没有历史包袱。"""
    return int.from_bytes(hashlib.blake2b(salt + as_bytes(item), digest_size=8).digest(),
                          "little")


def double_positions(item, m, k, salt=b""):
    """两个摘要线性组合：`(h1 + i * h2) % m`，i 从 0 到 k-1。"""
    first = digest64(item, salt + b"\x01")
    step = digest64(item, salt + b"\x02") | 1
    return [(first + index * step) % m for index in range(k)]


def independent_positions(item, m, k, salt=b""):
    """每个位置一次独立摘要 —— 对照组，也是唯一有严格分析的那一种。"""
    return [digest64(item, salt + bytes((index,))) % m for index in range(k)]


def splitmix_positions(item, m, k, salt=b""):
    """一次摘要 + splitmix64 展开成 k 个位置：最便宜的写法。"""
    state = digest64(item, salt)
    positions = []
    for _ in range(k):
        state = (state + GOLDEN) & MASK64
        value = state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
        positions.append((value ^ (value >> 31)) % m)
    return positions


POSITIONS = {
    "double": double_positions,
    "independent": independent_positions,
    "splitmix": splitmix_positions,
}
