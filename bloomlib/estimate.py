"""布隆过滤器的尺寸公式。

`optimal_m` / `optimal_k` 给出理论解，`predicted_fp` 给出理论误判率，
`estimated_n` 从占用率反推塞了多少元素。`benchmarks/` 的全部意义就是检查这四个函数
在真实哈希（而不是理想随机 oracle）上到底准不准。
"""

import math

LN2 = math.log(2.0)
LN2_SQUARED = LN2 * LN2


def optimal_m(capacity, false_positive):
    """装下 `capacity` 个元素并把误判压到 `false_positive` 需要多少 bit。"""
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if not 0.0 < false_positive < 1.0:
        raise ValueError("false positive rate must be inside (0, 1)")
    return max(8, int(math.ceil(-capacity * math.log(false_positive) / LN2_SQUARED)))


def optimal_k(m, capacity):
    """给定 bit 数时的最佳哈希函数个数：`(m / n) * ln 2`，四舍五入。"""
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    return max(1, int(round((m / float(capacity)) * LN2)))


def predicted_fp(m, k, inserted):
    """`n` 个元素之后，一个从未插入过的元素被判存在的概率。"""
    if inserted <= 0:
        return 0.0
    return (1.0 - math.exp(-k * inserted / float(m))) ** k


def estimated_n(m, k, bits_set):
    """由置位数反推元素个数：`-(m / k) * ln(1 - bits_set / m)`。"""
    if bits_set == 0:
        return 0.0
    if bits_set >= m:
        raise ValueError("the array is full; the estimator has no signal left")
    return -(m / float(k)) * math.log(1.0 - bits_set / float(m))


def bits_per_item(m, inserted):
    return m / float(inserted) if inserted else float("inf")
