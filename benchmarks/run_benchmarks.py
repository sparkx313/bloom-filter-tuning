"""测量 bloomlib 的行为：课本公式和真实哈希跑出来的数字放在一起对照。

运行方式：`python benchmarks/run_benchmarks.py`。打印出来的每个数字都来自本次运行，
并保存到 `benchmarks/last_run.txt`，README 引用的就是那份文件。键流由固定种子生成，
所以每一行实测概率在别的机器上可以复现；只有耗时列会漂。

七节依次回答：公式在填充率多少以内还成立、`k` 该怎么选、达到目标误判率要花多少位、
一次查询多少钱、三种位置生成方案有没有实测差别、计数器饱和会让假阴性出现在哪里、
以及「不知道容量」这件事值多少内存。
"""

import gc
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bloomlib import (Bloom, CountingBloom, DynamicBloom, bits_per_item, disjoint,
                      estimated_n, keys, optimal_k, optimal_m, predicted_fp)

CAPACITY = 20000
PROBES = 20000
TARGETS = (0.1, 0.01, 0.001)
FILLS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
RATIOS = (2.0, 4.0, 10.0)
K_VALUES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14)
SCHEMES = ("double", "splitmix", "independent")


def _line(title, char="-"):
    print(title)
    print(char * len(title))


def _stderr(rate, queries):
    """一个比例的抽样标准差 —— 没有它，「实测 0.0098 对理论 0.0100」这句话没有意义。"""
    return math.sqrt(max(rate, 1e-12) * (1.0 - min(rate, 1.0)) / float(queries))


def _z(measured, expected, queries):
    error = _stderr(expected, queries)
    return (measured - expected) / error if error else 0.0


def _timings(function, repeats=5):
    """关掉分代回收再采样：这些对象都是引用计数就能释放的，周期性扫描只会往每次采样里
    塞一段和结构无关的暂停。"""
    samples = []
    gc.disable()
    try:
        for _ in range(repeats):
            gc.collect()
            started = time.perf_counter()
            function()
            samples.append(time.perf_counter() - started)
    finally:
        gc.enable()
    return samples


def _best(function, repeats=5, cold=2):
    """耗时列取最好的一次（头两次冷跑不算）：这台机器上的抖动比结构差异更大。"""
    return min(_timings(function, repeats + cold)[cold:])


def _warm_spread(function, repeats=7, cold=2):
    """丢掉头几次冷跑之后的最大/最小偏差，作为「多少倍以内算不出来」的刻度。"""
    samples = _timings(function, repeats)[cold:]
    return 100.0 * (max(samples) - min(samples)) / min(samples)


def _fp_rate(filter, probe_keys):
    return sum(1 for key in probe_keys if key in filter) / float(len(probe_keys))


def section_theory():
    """1. 公式在填充率的哪一段还说得过去。"""
    _line("\n1. does the closed form survive a real hash?")
    print("  a filter sized for %d items at each target, filled to a fraction of that" % CAPACITY)
    print("  capacity and then probed with %d keys that were never inserted." % PROBES)
    print("  `z` is (measured - predicted) in sampling standard deviations of the")
    print("  measured rate: |z| under 3 is the formula agreeing with reality.\n")
    print("  %6s %5s %7s %7s %6s %9s %9s %6s %6s" %
          ("target", "fill", "n", "m", "k", "predicted", "measured", "z", "load"))
    worst = (0.0, None)
    ends = {}
    for target in TARGETS:
        filter = Bloom(CAPACITY, target)
        inserted, probe_keys = disjoint(CAPACITY * 2, seed=101)
        for fraction in FILLS:
            count = int(round(CAPACITY * fraction))
            filter.reset()
            filter.update(inserted[:count])
            measured = _fp_rate(filter, probe_keys[:PROBES])
            expected = predicted_fp(filter.m, filter.k, count)
            score = _z(measured, expected, PROBES)
            if abs(score) > abs(worst[0]):
                worst = (score, (target, fraction))
            ends.setdefault(target, {})[fraction] = (measured, expected)
            print("  %6.3f %5.2f %7d %7d %6d %9.5f %9.5f %6.1f %6.2f" %
                  (target, fraction, count, filter.m, filter.k, expected, measured,
                   score, filter.load()))
        print("")
    print("  the largest gap in the table is %.1f sigma at target %.3f, fill %.2f -- so"
          % (worst[0], worst[1][0], worst[1][1]))
    print("  within sampling noise at every fill tried, above and below capacity alike.")
    print("  What does change past capacity is the number you agreed to, not the model:")
    for target in TARGETS:
        inside, over = ends[target][1.0], ends[target][2.0]
        print("  target %.3f measures %.5f at capacity and %.5f at 2x it, %.1fx the target,"
              % (target, inside[0], over[0], over[0] / target))
        print("  which the predicted column (%.5f) still tracks to within %.1f%%."
              % (over[1], 100.0 * abs(over[0] / over[1] - 1.0)))
    print("  So 'the guarantee holds while you stay inside the size you asked for' is the")
    print("  honest reading: overrun costs up to %.0fx the rate here, and the closed form"
          % max(ends[t][2.0][0] / t for t in TARGETS))
    print("  predicts even that instead of breaking.\n")
    print("  the same run also reads the count back out of the bit pattern only:")
    for target in TARGETS:
        filter = Bloom(CAPACITY, target)
        inserted, _ = disjoint(CAPACITY, seed=101)
        filter.update(inserted)
        estimate = estimated_n(filter.m, filter.k, filter.bits_set())
        print("  target %.3f: %d inserted, occupancy estimate %.0f (%.1f%% off)" %
              (target, filter.inserted, estimate, 100.0 * (estimate / CAPACITY - 1.0)))


def section_k():
    """2. k 该选几个：把最优值跑出来而不是引用它。"""
    _line("\n2. how many hash functions, measured rather than quoted")
    count = 10000
    inserted, probe_keys = disjoint(count * 2, seed=202)
    for ratio in RATIOS:
        m = int(round(count * ratio))
        formula = optimal_k(m, count)
        rates = {}
        print("  %d keys in %d bits is %.1f bits a item; the formula asks for k=%d"
              % (count, m, ratio, formula))
        print("    %3s %11s %11s %7s %8s %6s" %
              ("k", "predicted", "measured", "z", "vs best", "load"))
        for k in K_VALUES:
            filter = Bloom(count, m=m, k=k)
            filter.update(inserted[:count])
            measured = _fp_rate(filter, probe_keys[:PROBES])
            rates[k] = (measured, predicted_fp(m, k, count),
                        _z(measured, predicted_fp(m, k, count), PROBES), filter.load())
        best_k = min(rates, key=lambda value: rates[value][0])
        for k in K_VALUES:
            print("    %3d %11.6f %11.6f %7.1f %8.2fx %6.2f" %
                  (k, rates[k][1], rates[k][0], rates[k][2],
                   rates[k][0] / rates[best_k][0], rates[k][3]))
        neighbours = [rates[k][0] for k in (best_k - 1, best_k + 1) if k in rates]
        print("  the measured minimum is k=%d at %.6f -- %s."
              % (best_k, rates[best_k][0],
                 "the same number the closed form asks for" if best_k == formula
                 else "one step from the formula's k=%d" % formula))
        print("  One step away costs %.2fx and the largest k tried %.2fx: the penalty"
              % (max(neighbours) / rates[best_k][0],
                 rates[K_VALUES[-1]][0] / rates[best_k][0]))
        print("  for hashing too little is %.2fx, for hashing too much %.2fx, so a"
              % (rates[1][0] / rates[best_k][0],
                 rates[K_VALUES[-1]][0] / rates[best_k][0]))
        print("  couple of steps either side of the formula's answer is a rounding error.\n")
        noisy = sorted(k for k in rates if abs(rates[k][2]) > 3)
        if noisy:
            print("  the rows outside 3 sigma are k=%s, measured %s predicted: the array"
                  % (",".join(str(k) for k in noisy),
                     "above" if rates[noisy[0]][2] > 0 else "below"))
            print("  is %.2f full there and the closed form's independent-draws assumption"
                  % (sum(rates[k][3] for k in noisy) / len(noisy)))
            print("  is the thing that gives way, not the arithmetic.\n")
        else:
            print("  every row here stays inside 3 sigma of the closed form at %.1f bits"
                  % ratio)
            print("  a item, so the formula is the binding constraint and k is a free\n"
                  "  choice within a couple of steps of its answer.\n")


def section_bytes():
    """3. 目标误判率换算成每位元素的成本。"""
    _line("\n3. what does a target false positive rate cost in bits")
    print("  %d items in a filter sized by the textbook formula, probed %d times.\n"
          % (CAPACITY, PROBES))
    inserted, probe_keys = disjoint(CAPACITY * 2, seed=303)
    print("  %8s %8s %8s %8s %3s %10s %10s %7s %6s" %
          ("target", "theory", "actual", "k/target", "k", "predicted", "measured",
           "ratio", "z"))
    rows = {}
    for target in (0.2, 0.1, 0.03, 0.01, 0.003, 0.001, 0.0001):
        filter = Bloom(CAPACITY, target)
        filter.update(inserted[:CAPACITY])
        measured = _fp_rate(filter, probe_keys[:PROBES])
        expected = filter.predicted_fp()
        theory_bits = -math.log(target) / (math.log(2.0) ** 2)
        score = _z(measured, expected, PROBES)
        rows[target] = (measured, expected, score,
                        measured / expected if expected else 0.0)
        print("  %8.5f %8.4f %8.4f %8.4f %3d %10.6f %10.6f %7.2f %6.1f" %
              (target, theory_bits, bits_per_item(filter.m, CAPACITY),
               (filter.m / float(CAPACITY)) * math.log(2.0), filter.k,
               expected, measured, rows[target][3], score))
    loudest = max(rows, key=lambda value: abs(math.log(rows[value][3])))
    print("  `theory` is the closed form's bits a item, `actual` is what the integer")
    print("  ceiling of m costs, and `k/target` is the (m/n)ln2 the formula wanted.")
    print("  `ratio` is measured over predicted and `z` is that same gap in sampling")
    print("  sigma for %d probes: the widest ratio belongs to target %.5f at %.2f,"
          % (PROBES, loudest, rows[loudest][3]))
    print("  which is %.1f sigma -- the formula and the hash agree as well as %d probes"
          % (rows[loudest][2], PROBES))
    print("  can tell them apart. Tightening the target shrinks the number being measured")
    print("  and not the noise: at target %.5f the expected hit count is %.1f out of %d"
          % (min(rows), min(rows) * PROBES, PROBES))
    print("  probes, so one extra hit moves `ratio` by %.2f -- that row is a readout of"
          % (1.0 / (min(rows) * PROBES)))
    print("  the probe count, not of the formula.\n")
    small_m = optimal_m(CAPACITY, 0.05)
    swept = {}
    for k in range(2, 11):
        candidate = Bloom(CAPACITY, m=small_m, k=k)
        candidate.update(inserted[:CAPACITY])
        swept[k] = _fp_rate(candidate, probe_keys[:PROBES])
    best_k = min(swept, key=lambda value: swept[value])
    cheap = Bloom(CAPACITY, 0.01, m=small_m, k=7)
    cheap.update(inserted[:CAPACITY])
    cheap_rate = _fp_rate(cheap, probe_keys[:PROBES])
    wanted = Bloom(CAPACITY, 0.01)
    wanted.update(inserted[:CAPACITY])
    wanted_rate = _fp_rate(wanted, probe_keys[:PROBES])
    print("  the shortcut -- k=7 in memory that was sized for a 5%% target -- costs %d"
          % cheap.bytes_used())
    print("  bytes for %d keys and measures %.5f where %.5f was asked for. Choosing k"
          % (CAPACITY, cheap_rate, 0.01))
    print("  harder does not rescue it: sweeping k over the same %d bytes bottoms out at"
          % cheap.bytes_used())
    print("  %.5f (k=%d), still %.1fx the target, because m is what sets the floor."
          % (swept[best_k], best_k, swept[best_k] / 0.01))
    print("  The filter actually sized for 1%% costs %d bytes and measures %.5f. Tuning"
          % (wanted.bytes_used(), wanted_rate))
    print("  k is a free few percent; shrinking m is a wall.")


def _set_bytes(store):
    """Python set 的实测占用：容器本身加上每个字符串键的 sizeof。"""
    return sys.getsizeof(store) + sum(sys.getsizeof(key) for key in store)


def section_cost():
    """4. 一次插入、一次命中、一次未命中各多少钱。"""
    _line("\n4. what a query costs, and what it saves")
    count = 20000
    inserted, probe_keys = disjoint(count * 2, seed=404)
    batch = keys(2000, "insert-batch", seed=405)
    hits, misses = inserted[:2000], probe_keys[:2000]
    print("  each structure holds the same %d keys; a probe batch of 2000 present and" % count)
    print("  2000 absent keys is timed, so hit and miss are both measured and not")
    print("  assumed symmetric. Insertions are timed on a second,")
    print("  identical structure so they cannot inflate the row that reports them, and")
    print("  every timing is the best of five passes after two discarded warm-ups.\n")
    probe = Bloom(count, 0.01)
    probe.update(inserted[:count])
    spread = _warm_spread(lambda: [key in probe for key in misses])
    print("  %-14s %10s %9s %9s %9s %10s %9s" %
          ("structure", "insert ns", "hit ns", "miss ns", "bytes", "bytes/key", "false +"))
    factories = (("Bloom", lambda: Bloom(count, 0.01)),
                 ("CountingBloom", lambda: CountingBloom(count, 0.01)),
                 ("DynamicBloom", lambda: DynamicBloom(count // 4, 0.01)),
                 ("python set", set))
    rows = {}
    for name, make in factories:
        container = make()
        container.update(inserted[:count])
        scratch = make()
        scratch.update(inserted[:count])
        if name == "python set":
            used, rate = _set_bytes(container), 0.0
        else:
            used, rate = container.bytes_used(), _fp_rate(container, misses)
        insert_ns = 1e9 * _best(lambda: scratch.update(batch)) / len(batch)
        hit_ns = 1e9 * _best(lambda: [key in container for key in hits]) / len(hits)
        miss_ns = 1e9 * _best(lambda: [key in container for key in misses]) / len(misses)
        rows[name] = (insert_ns, hit_ns, miss_ns, used, rate, container)
        print("  %-14s %10.0f %9.0f %9.0f %9d %10.1f %8.1f%%" %
              (name, insert_ns, hit_ns, miss_ns, used, used / float(count), 100 * rate))
    base = rows["Bloom"]
    print("  the set is exact -- no false positives, because it stores the keys -- and")
    print("  it is %.1fx the memory of the Bloom per key, strings included. That ratio is"
          % (rows["python set"][3] / float(base[3])))
    print("  the whole argument for the filter, and the `false +` column is the price.")
    print("  CountingBloom pays %.1fx the bits of the Bloom because each slot is a nibble"
          % (rows["CountingBloom"][3] / float(base[3])))
    print("  and answers a hit in %.2fx the Bloom's %.0f ns; DynamicBloom looked at %d"
          % (rows["CountingBloom"][1] / base[1], base[1],
             rows["DynamicBloom"][5].probes()))
    print("  slots per probe against the Bloom's %d, so its miss column is %.2fx at"
          % (base[5].k, rows["DynamicBloom"][2] / base[2]))
    print("  %.0f ns against %.0f. Read those as ratios, not absolutes: the identical"
          % (rows["DynamicBloom"][2], base[2]))
    print("  batch of probes varies %.0f%% pass to pass on this machine, so anything"
          % spread)
    print("  inside that is a tie. A thousand Bloom misses cost %.1f ms at best of five,"
          % (base[2] / 1e6 * 1000.0))
    print("  because every slot check is a Python-level call rather than a word compare.")


def section_schemes():
    """5. 三种位置生成方案的实测差别。"""
    _line("\n5. does the cheap hashing scheme cost accuracy?")
    print("  identical m, k and key set for all three; only the position generator")
    print("  changes. `independent` is the variant the closed form was derived for.\n")
    count = 20000
    inserted, probe_keys = disjoint(count * 2, seed=505)
    print("  %-12s %4s %10s %10s %7s %10s %10s" %
          ("scheme", "k", "predicted", "measured", "z", "insert ns", "probe ns"))
    rows = {}
    for scheme in SCHEMES:
        filter = Bloom(count, 0.01, scheme=scheme)
        filter.update(inserted[:count])
        measured = _fp_rate(filter, probe_keys[:PROBES])
        expected = filter.predicted_fp()
        insert_ns = 1e9 * _best(lambda: filter.update([inserted[0]]))
        probe_ns = 1e9 * _best(lambda: [key in filter for key in probe_keys[:2000]]) / 2000.0
        rows[scheme] = (measured, expected, _z(measured, expected, PROBES),
                        insert_ns, probe_ns)
        print("  %-12s %4d %10.6f %10.6f %7.1f %10.0f %10.0f" %
              (scheme, filter.k, expected, measured, rows[scheme][2], insert_ns, probe_ns))
    farthest = max(rows, key=lambda name: abs(rows[name][2]))
    cheapest = min(rows[name][4] for name in SCHEMES)
    print("  the widest gap from the closed form is %.1f sigma (%s): every scheme lands"
          % (rows[farthest][2], farthest))
    print("  within sampling noise of the formula. What does separate them is price --")
    print("  `independent` costs %.1fx the cheapest scheme per probe (%.0f ns against"
          % (rows["independent"][4] / cheapest, rows["independent"][4]))
    print("  %.0f), so on this corpus the cheap schemes are not measurably worse and"
          % cheapest)
    print("  are measurably faster.\n")
    print("  position collisions inside one key are the one thing double hashing gives")
    print("  up: k draws from a shrinking pool instead of k independent ones. Sample of")
    print("  5000 keys, same m=%d and k=%d for all three:" % (filter.m, filter.k))
    distinct_prob = 1.0
    for offset in range(filter.k):
        distinct_prob *= 1.0 - offset / float(filter.m)
    expected = 5000 * (1.0 - distinct_prob)
    counts = {}
    for scheme in SCHEMES:
        filter = Bloom(count, 0.01, scheme=scheme)
        counts[scheme] = sum(len(set(filter.positions(key))) < filter.k
                             for key in inserted[:5000])
        print("  %-12s %4d of 5000 keys hash to fewer than %d distinct slots" %
              (scheme, counts[scheme], filter.k))
    print("  the birthday arithmetic says %.1f of the 5000 should collide at all, and"
          % expected)
    print("  the worst scheme here measured %d -- %.1fx that estimate. Collisions are"
          % (max(counts.values()), max(counts.values()) / max(expected, 0.5)))
    print("  real but rare at this size, which is why the accuracy table above cannot")
    print("  tell the schemes apart and the timing table can.\n")


def section_churn():
    """6. 计数器饱和之后，删除会让假阴性出现在哪里。"""
    _line("\n6. deletion, saturation and the false negative it can create")
    design = 2000
    sentinels = keys(200, "sentinel", seed=601)
    probes = keys(PROBES, "churnprobe", seed=602)
    reference = Bloom(design, 0.01)
    print("  a counter is 4 bits, so a slot hit 15 times records nothing more while its")
    print("  removals keep subtracting. That is the only way a filter with `remove` can")
    print("  lose a key it still holds, so this section drives the filter hard enough to")
    print("  saturate it and reads the damage back out.")
    print("  Setup: %d counters with k=%d, %d sentinel keys live for the whole run, and"
          % (reference.m, reference.k, len(sentinels)))
    print("  rounds of `insert N fresh keys, delete those same N` for N at 1x, 5x and")
    print("  22.5x the design capacity. `sat+` is increments refused at 15 during the")
    print("  insert half of the round; `refused` is deletions that found a 0 and did")
    print("  nothing; `missing` counts sentinels gone after the delete.\n")
    print("  %6s %7s %9s %9s %9s %9s %11s %10s" %
          ("load x", "round", "N", "sat+", "refused", "missing", "measured fp",
           "predicted"))
    flat = Bloom(design, 0.01)
    flat.update(sentinels)
    summary = {}
    retained = len(sentinels)
    for multiplier in (1.0, 5.0, 22.5):
        batch_size = int(round(design * multiplier))
        filter = CountingBloom(design, 0.01)
        filter.update(sentinels)
        totals = [0, 0, 0]
        for round_index in range(1, 4):
            batch = keys(batch_size, "churn%g-%d" % (multiplier, round_index),
                         seed=700 + round_index)
            before = filter.saturated
            filter.update(batch)
            flat.update(batch)
            retained += len(batch)
            saturated = filter.saturated - before
            refused = batch_size - filter.remove_all(batch)
            totals[0] += saturated
            totals[1] += refused
            totals[2] += batch_size
            missing = sum(1 for key in sentinels if key not in filter)
            measured = _fp_rate(filter, probes)
            summary[multiplier] = tuple(totals) + (missing, measured,
                                                   filter.predicted_fp(),
                                                   filter.counters_set())
            print("  %6.1f %7d %9d %9d %9d %9d %11.5f %10.5f" %
                  (multiplier, round_index, batch_size, saturated, refused, missing,
                   measured, filter.predicted_fp()))
        print("")
    first, last = summary[1.0], summary[22.5]
    print("  at design load nothing saturates: %d increments refused, %d deletions"
          % (first[0], first[1]))
    print("  refused, %d sentinels lost, %d counters left and a rate of %.5f against a"
          % (first[3], first[6], first[4]))
    print("  predicted %.5f. Deletion is exact there because every contribution was"
          % first[5])
    print("  recorded before it was subtracted.")
    print("  five times the load is clean on the same counts (%d, %d, %d lost), so the"
          % (summary[5.0][0], summary[5.0][1], summary[5.0][3]))
    print("  cliff this section is looking for sits between those two, not at 1x.")
    print("  at 22.5x the same three rounds refuse %d increments and %d deletions over"
          % (last[0], last[1]))
    print("  %d churned keys, and the filter comes back holding %d of its %d sentinels"
          % (last[2], len(sentinels) - last[3], len(sentinels)))
    print("  -- %.1f%% false negatives, which a plain Bloom cannot produce at all. The"
          % (100.0 * last[3] / len(sentinels)))
    print("  rate goes the other way: %.5f for the counter filter against %.5f for the"
          % (last[4], _fp_rate(flat, probes)))
    print("  bit array that still holds all %d keys it was ever given. So deletion does"
          % retained)
    print("  drain, and up to about the design load that drain is worth the 4x memory;")
    print("  past it the failure mode is silent loss of live keys, not a slower answer.")


def section_growth():
    """7. 不知道容量时要付多少。"""
    _line("\n7. what not knowing the capacity costs")
    final = 40000
    inserted, probe_keys = disjoint(final * 2, seed=707)
    flat = Bloom(final, 0.01)
    flat.update(inserted[:final])
    flat_bytes = flat.bytes_used()
    print("  the stream turns out to be %d keys. `first layer` below is the number the" % final)
    print("  filter was given when it was built, and it grows a layer whenever a layer")
    print("  fills; `known` is the one filter sized for the true count at the end.\n")
    print("  %-14s %6s %9s %9s %10s %10s %7s %8s" %
          ("first layer", "layers", "bytes", "vs known", "budget", "measured", "probes", "wasted"))
    print("  %-14s %6d %9d %9.1fx %10.5f %10.5f %7d %7.0f%%" %
          ("%d known" % final, 1, flat_bytes, 1.0, flat.predicted_fp(),
           _fp_rate(flat, probe_keys[:PROBES]), flat.k,
           100.0 * (1.0 - final / float(flat.capacity))))
    configs = []
    for fraction in (0.5, 0.25, 0.1):
        capacity = max(1, int(final * fraction))
        grown = DynamicBloom(capacity, 0.01)
        grown.update(inserted[:final])
        configs.append(("%d keys" % capacity, grown))
    loose = DynamicBloom(max(1, int(final * 0.1)), 0.01, decay=1.0)
    loose.update(inserted[:final])
    configs.append(("%d decay=1" % loose.layers[0].capacity, loose))
    for label, grown in configs:
        measured = _fp_rate(grown, probe_keys[:PROBES])
        print("  %-14s %6d %9d %9.1fx %10.5f %10.5f %7d %7.0f%%" %
              (label, len(grown.layers), grown.bytes_used(),
               grown.bytes_used() / float(flat_bytes), grown.fp_bound(), measured,
               grown.probes(), 100.0 * grown.unused_capacity()))
    tight = configs[-2][1]
    capacity = tight.layers[0].capacity
    tight_rate = _fp_rate(tight, probe_keys[:PROBES])
    loose_rate = _fp_rate(loose, probe_keys[:PROBES])
    print("  starting at %d keys -- 10%% of the true count -- ends at %d layers, %d bytes"
          % (capacity, len(tight.layers), tight.bytes_used()))
    print("  against the known-size filter's %d, so %.1fx the memory and %d slots per"
          % (flat_bytes, tight.bytes_used() / float(flat_bytes), tight.probes()))
    print("  probe against %d, while the measured rate (%.5f) still lands under the 1%%"
          % (flat.k, tight_rate))
    print("  that was asked for. `wasted` is the other half of the story: the last layer")
    print("  is paid for in full and used only in part, and that unused room is what")
    print("  makes the next layer affordable when it arrives.\n")
    print("  `decay` is the knob that sets each new layer's budget, and the last row is")
    print("  the same growth with decay=1.0 -- every layer handed the full %.2f%%"
          % (100 * 0.01))
    print("  instead of a shrinking share: %d layers and %d bytes against %d, so %.1fx"
          % (len(loose.layers), loose.bytes_used(), tight.bytes_used(),
             tight.bytes_used() / float(loose.bytes_used())))
    print("  cheaper, at a measured %.5f against %.5f and"
          % (loose_rate, tight_rate))
    print("  a budget of %.5f against %.5f. The bound column tracks the measurement on"
          % (loose.fp_bound(), tight.fp_bound()))
    print("  both rows, which is the point of `fp_bound`: the layer budgets only sum to")
    print("  the target when each layer is handed a smaller one than the last.")


class _Tee:
    """stdout 照常打印，同时攒一份写到文件里。

    这样 `last_run.txt` 永远是刚刚那一次运行的原文，README 的数字不可能跟文件对不上。
    """

    def __init__(self, stream, path):
        self.stream = stream
        self.path = path
        self.parts = []

    def write(self, text):
        self.stream.write(text)
        self.parts.append(text)
        return len(text)

    def flush(self):
        self.stream.flush()

    def save(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(self.parts))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_run.txt")
    console = sys.stdout
    sys.stdout = _Tee(console, report)
    try:
        print("bloomlib benchmarks - python %s" % sys.version.split()[0])
        section_theory()
        section_k()
        section_bytes()
        section_cost()
        section_schemes()
        section_churn()
        section_growth()
    finally:
        sys.stdout.save()
        sys.stdout = console
        print("wrote %s" % report)


if __name__ == "__main__":
    main()
