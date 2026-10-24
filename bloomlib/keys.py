"""确定性键流。

插入集和查询集必须**互不相交**，否则测出来的「误判率」里混着真正命中的元素。这里用前缀
把两个集合隔开，用固定种子让每个键里的随机段在每台机器上一致，所以 `last_run.txt` 里
每一行实测概率都是可复现的。
"""

import random


def keys(count, prefix, seed=0):
    """`count` 个形如 `prefix/0001a3f9b7c2/0` 的字符串键。"""
    rng = random.Random(seed)
    return ["%s/%012x/%d" % (prefix, rng.getrandbits(48), index) for index in range(count)]


def disjoint(count, seed=0, insert_prefix="in", probe_prefix="out"):
    """返回 `(插入键, 从未插入过的键)`，两组前缀不同所以必然不相交。"""
    return keys(count, insert_prefix, seed), keys(count, probe_prefix, seed + 1)
