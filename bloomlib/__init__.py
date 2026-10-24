"""只用标准库实现的布隆过滤器家族，以及它的实测行为。

`Bloom` 是只增不删的位数组，`CountingBloom` 用 4 bit 计数器换来删除，`DynamicBloom`
用分层增长应对未知容量。`estimate` 里是课本上的尺寸公式和误判率公式，`benchmarks/`
的任务是把这些公式和真实哈希跑出来的数字放在同一张表里对照 —— 一致的地方、以及
饱和计数器让假阴性出现的地方。
"""

from .bloom import Bloom
from .counting import CountingBloom
from .dynamic import DynamicBloom
from .estimate import (bits_per_item, estimated_n, optimal_k, optimal_m,
                       predicted_fp)
from .hashing import (POSITIONS, digest64, double_positions,
                      independent_positions, splitmix_positions)
from .keys import disjoint, keys

__version__ = "0.1.0"

__all__ = ["Bloom", "CountingBloom", "DynamicBloom", "POSITIONS", "bits_per_item",
           "digest64", "disjoint", "double_positions", "estimated_n", "independent_positions",
           "keys", "optimal_k", "optimal_m", "predicted_fp", "splitmix_positions"]
