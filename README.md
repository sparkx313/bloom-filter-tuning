# bloom-filter-tuning

Three Bloom filters — plain, counting, and dynamically growing — and a benchmark that
puts the textbook formulas next to what a real `blake2b`-backed hash actually does. The
target problem is the one every cache-lookup, crawler dedupe and "have we seen this
URL?" service hits: *how many bits do I pay so that 1 in 100 absent keys answers
"present", and what happens when I ignore the answer*.

This is a measurement repo, not a folklore repo. Five claims were checked and each one
is generated from the numbers in [`benchmarks/last_run.txt`](benchmarks/last_run.txt) —
the verbatim output of one run — rather than asserted: the closed form tracks a real hash
to within ~2 sigma at every fill from 0.25x to 2x capacity; the measured best `k`
coincides with `(m/n)ln2`; memory — not `k` — sets the accuracy floor; `CountingBloom`
deletion is *exact* up to ~5x design load and then silently destroys 84.5% of live keys
once the 4-bit counters saturate; and not knowing your capacity costs ~2.2x the memory.
Pure CPython standard library, zero dependencies, 51 tests.

## What it does / does not do

Does:
- `Bloom` (append-only bit array), `CountingBloom` (4-bit saturating counters, so
  `remove` exists), `DynamicBloom` (layers, so unknown capacity works) — one interface:
  `add` / `update` / `in` / `bytes_used`.
- The sizing math as functions you can call: `optimal_m`, `optimal_k`, `predicted_fp`,
  `estimated_n`, `bits_per_item`.
- Three position generators to compare: `double` (Kirsch–Mitzenmacher), `splitmix`
  (one digest, splitmix64 expansion), `independent` (per-index `blake2b` salts).
- Seven measured sections: theory-vs-reality, `k` sweep, cost of a tighter target,
  per-query price, hashing scheme, deletion saturation, layered growth.

Does not:
- It is not fast. Everything is a Python-level call per slot; a thousand Bloom misses
  costs milliseconds (Section 4). For real traffic this is a *reference implementation*
  whose value is the measurement.
- No `remove` on `Bloom` — that is the point of it, and Section 6 shows why the variant
  that deletes has a failure mode it cannot have.
- Timings are not portable. The benchmark prints its own pass-to-pass spread as the floor
  below which "faster" is not a claim; the count/byte/rate columns are seeded and
  reproduce exactly.

## Install

No install step: standard library only (Python 3.9+, verified on CPython 3.11.9).

```bash
git clone <this repo> && cd bloom-filter-tuning
python -u -m unittest discover -s tests -p 'test_*.py' -t tests   # 51 tests
```

`pip install -e .` registers the `bloomlib` package if you prefer that.

## Quick start

Every block below ran on CPython 3.11.9; the comments are its real output.

Sizing, from the closed form:

```python
from bloomlib import Bloom, optimal_k, optimal_m, predicted_fp

optimal_m(20000, 0.01)          # 191702  = ceil(-n ln p / (ln 2)^2)
optimal_k(191702, 20000)        # 7       = round((m/n) ln 2)
predicted_fp(191702, 7, 20000)  # 0.010039010484203732
Bloom(20000, 0.01).bytes_used() # 23963   bytes = 1.2 per key
```

Membership, and the asymmetry that defines the structure:

```python
from bloomlib import Bloom, disjoint

inserted, probes = disjoint(40000, seed=1)
bloom = Bloom(20000, 0.01)
bloom.update(inserted[:20000])
all(key in bloom for key in inserted[:20000])     # True  — no false negatives, ever
sum(1 for key in probes[:20000] if key in bloom)  # 196   — 0.0098 against a 0.01 target
bloom.load()                                      # 0.52  — the fraction of bits set
```

Deletion, and the counter ceiling that makes it conditional:

```python
from bloomlib import CountingBloom

counting = CountingBloom(2000, 0.01)
counting.add("session:1")
"session:1" in counting       # True
counting.remove("session:1")
"session:1" in counting       # False — exact while nothing saturated
counting.saturated            # 0     — increments refused at 15
```

Unknown capacity:

```python
from bloomlib import DynamicBloom

dynamic = DynamicBloom(10000, 0.01)
dynamic.update(["k%d" % i for i in range(40000)])
len(dynamic.layers)           # 3
dynamic.bytes_used()          # 114528  (a filter sized knowing 40000 costs 47926)
dynamic.probes()              # 24      slot checks per query
round(dynamic.fp_bound(), 5)  # 0.00753 — union bound over the layers
```

Counting how much you inserted from the bit pattern alone:

```python
from bloomlib import Bloom

bloom = Bloom(20000, 0.001)
bloom.update(["session:%d" % i for i in range(20000)])
bloom.bits_set()              # 144214 of m=287552, load 0.5
bloom.estimated_inserted()    # 20019.3 — +0.1% off, without the counter
```

Choosing the hash:

```python
from bloomlib import Bloom

for scheme in ("double", "splitmix", "independent"):
    filter = Bloom(20000, 0.01, scheme=scheme)
    filter.add("user:1")
    print(scheme, filter.k, "user:1" in filter, "never-inserted" in filter)
# double 7 True False
# splitmix 7 True False
# independent 7 True False
```

## Measured results

Tables are copied line-for-line from `benchmarks/last_run.txt`, headers included.

### 1. Does the closed form survive a real hash?

A filter sized for 20000 items, filled to a fraction of that, probed with 20000 keys that
were never inserted. `z` is (measured − predicted) in sampling sigma of the measured rate.

```text
  target  fill       n       m      k predicted  measured      z   load
   0.100  0.25    5000   95851      3   0.00304   0.00295   -0.2   0.15
   0.100  0.50   10000   95851      3   0.01941   0.02055    1.2   0.27
   0.100  0.75   15000   95851      3   0.05260   0.05325    0.4   0.37
   0.100  1.00   20000   95851      3   0.10071   0.09980   -0.4   0.46
   0.100  1.25   25000   95851      3   0.15986   0.15985   -0.0   0.54
   0.100  1.50   30000   95851      3   0.22583   0.22525   -0.2   0.61
   0.100  2.00   40000   95851      3   0.36407   0.36490    0.2   0.72
   0.001  1.00   20000  287552     10   0.00100   0.00140    1.8   0.50
   0.001  2.00   40000  287552     10   0.05721   0.05875    0.9   0.75
```

The largest gap in the table is 1.8 sigma, at target 0.001 and exactly design capacity —
so within sampling noise at every fill tried, above and below capacity alike. What changes
past capacity is the number you agreed to, not the model: `0.100` measures 0.09980 at
capacity and 0.36490 at 2x it (3.6x the target), `0.001` measures 0.00140 at capacity and
0.05875 at 2x it (58.7x the target), and in both cases the `predicted` column is still
within 2.7%. The honest reading is *"the guarantee holds while you stay inside the size you
asked for"* — overrun is up to 59x here, and the formula predicts even that.

The same run also reads the inserted count back out of the bit pattern only:

```text
  target 0.100: 20000 inserted, occupancy estimate 19881 (-0.6% off)
  target 0.010: 20000 inserted, occupancy estimate 19961 (-0.2% off)
  target 0.001: 20000 inserted, occupancy estimate 19986 (-0.1% off)
```

### 2. How many hash functions, measured

10000 keys in 100000 bits is 10.0 bits an item, so the formula asks for k=7:

```text
  10000 keys in 100000 bits is 10.0 bits a item; the formula asks for k=7
      k   predicted    measured       z  vs best   load
      1    0.095163    0.095600     0.2    11.05x   0.10
      2    0.032859    0.034200     1.1     3.95x   0.18
      3    0.017411    0.018000     0.6     2.08x   0.26
      4    0.011813    0.011200    -0.8     1.29x   0.33
      5    0.009431    0.008850    -0.8     1.02x   0.39
      6    0.008436    0.008750     0.5     1.01x   0.45
      7    0.008194    0.008650     0.7     1.00x   0.50
      8    0.008455    0.009150     1.1     1.06x   0.55
      9    0.009127    0.010850     2.6     1.25x   0.59
     10    0.010186    0.011050     1.2     1.28x   0.63
     12    0.013561    0.014400     1.0     1.66x   0.70
     14    0.018984    0.019150     0.2     2.21x   0.75
  the measured minimum is k=7 at 0.008650 -- the same number the closed form asks for.
```

The measured minimum is the formula's answer, and one step either side costs 1.06x — `k`
is a free few percent. The same holds at 2.0 and 4.0 bits an item, where the formula asks
for k=1 and k=3 and the tables bottom out there too:

```text
  10000 keys in 20000 bits is 2.0 bits a item; the formula asks for k=1
  the measured minimum is k=1 at 0.398700 -- the same number the closed form asks for.
```

Only the over-filled arrays disagree with the model, and they disagree in the direction the
model admits it cannot cover:

```text
  the rows outside 3 sigma are k=5,6,7,8,12, measured above predicted: the array
```

— at 2 bits an item those rows sit at 0.92–1.00 load, where the closed form's
independent-draws assumption is the thing that gives way, not the arithmetic.

### 3. What a tighter target costs in bits

```text
    target   theory   actual k/target   k  predicted   measured   ratio      z
   0.20000   3.3498   3.3498   2.3219   2   0.202106   0.207450    1.03    1.9
   0.10000   4.7925   4.7926   3.3219   3   0.100712   0.101200    1.00    0.2
   0.03000   7.2984   7.2984   5.0589   5   0.030004   0.029600    0.99   -0.3
   0.01000   9.5851   9.5851   6.6439   7   0.010039   0.011050    1.10    1.4
   0.00300  12.0910  12.0910   8.3808   8   0.003011   0.002300    0.76   -1.8
   0.00100  14.3776  14.3776   9.9658  10   0.001000   0.000900    0.90   -0.4
   0.00010  19.1701  19.1701  13.2877  13   0.000100   0.000150    1.50    0.7
```

Each factor-of-10 in tightness costs ~4.8 bits an item (9.59 → 14.38 → 19.17), which is
the linear-in-ln(1/p) the formula says and nothing else. `theory` is the closed form's bits
an item, `actual` is what the integer ceiling of `m` costs, and the widest `ratio` in the
table (target 0.00010, 1.50) is 0.7 sigma: at that target the expected hit count is 2.0 out
of 20000 probes, so one extra hit moves the ratio by 0.50. That row is a readout of the
probe count, not of the formula.

The shortcut everyone takes — `k=7` in memory sized for a looser target — was measured
too, together with a `k` sweep inside that same memory:

```text
  the shortcut -- k=7 in memory that was sized for a 5% target -- costs 15589
  bytes for 20000 keys and measures 0.06185 where 0.01000 was asked for. Choosing k
  harder does not rescue it: sweeping k over the same 15589 bytes bottoms out at
  0.04755 (k=4), still 4.8x the target, because m is what sets the floor.
  The filter actually sized for 1% costs 23963 bytes and measures 0.01105. Tuning
  k is a free few percent; shrinking m is a wall.
```

### 4. What a query costs, and what it saves

Same 20000 keys in each structure; 2000 present and 2000 absent keys probed; insertions
timed on a second identical structure; every timing best-of-five after two discarded
warm-ups, with the GC off.

```text
  structure       insert ns    hit ns   miss ns     bytes  bytes/key   false +
  Bloom               13379     17113     16204     23963        1.2      0.8%
  CountingBloom       25177     23415     20469     95851        4.8      0.7%
  DynamicBloom        15040     17901     42893     57265        2.9      0.6%
  python set             70       187       165   3486258      174.3      0.0%
```

```text
  it is 145.5x the memory of the Bloom per key, strings included. That ratio is
```

The set is exact and pays for it: 174.3 bytes a key against the Bloom's 1.2.
`CountingBloom` pays 4.0x the bits because each slot is a nibble and answers a hit in
1.37x the Bloom's time; `DynamicBloom` looks at 24 slots per probe against the Bloom's 7,
so its miss column is 2.65x. Read those as ratios: this run declares its own noise floor
(81% pass-to-pass on the identical batch), so anything inside that is a tie and the byte
columns are the only absolutes worth quoting.

### 5. Does the cheap hashing scheme cost accuracy?

Identical `m`, `k` and key set; only the position generator changes. `independent` is the
variant the closed form was derived for.

```text
  scheme          k  predicted   measured       z  insert ns   probe ns
  double          7   0.010039   0.009200    -1.2      42700      14317
  splitmix        7   0.010039   0.009850    -0.3      41900      15701
  independent     7   0.010039   0.010800     1.1      55300      30571
```

The widest gap from the closed form is -1.2 sigma, so on this corpus no scheme is
measurably worse than the theoretically-clean one, while `independent` costs 2.1x the
cheapest per probe. What double hashing *does* give up is collisions inside one key's `k`
draws, which the run counts against the birthday arithmetic:

```text
  double          0 of 5000 keys hash to fewer than 7 distinct slots
  splitmix        1 of 5000 keys hash to fewer than 7 distinct slots
  independent     1 of 5000 keys hash to fewer than 7 distinct slots
  the birthday arithmetic says 0.5 of the 5000 should collide at all, and
```

0.5 collisions expected, 1 measured for the worst scheme — real, rare at this size, and
far below what 20000 probes could detect. That is exactly why the accuracy table cannot
separate the schemes and the timing table can.

### 6. Deletion, saturation, and the false negative it can create

A counter is 4 bits: a slot hit 15 times records nothing more while its removals keep
subtracting. That is the only way a filter with `remove` can lose a key it still holds, so
this section drives the filter hard enough to saturate it and reads the damage back out.
Setup: 19171 counters, k=7, 200 sentinel keys that stay live the whole run, and rounds of
"insert N fresh keys, delete those same N" at 1x, 5x and 22.5x design capacity.

```text
  load x   round         N      sat+   refused   missing measured fp  predicted
     1.0       1      2000         0         0         0     0.00000    0.00000
     1.0       2      2000         0         0         0     0.00000    0.00000
     1.0       3      2000         0         0         0     0.00000    0.00000
     5.0       1     10000         0         0         0     0.00000    0.00000
     5.0       2     10000         0         0         0     0.00000    0.00000
     5.0       3     10000         0         0         0     0.00000    0.00000
    22.5       1     45000     46904     13222       175     0.14880    0.14780
    22.5       2     45000     82807     13165       168     0.15345    0.15349
    22.5       3     45000     91348     13255       169     0.16275    0.15832
```

At design load nothing saturates: 0 increments refused, 0 deletions refused, 0 sentinels
lost. Five times the load is clean on the same counts, so the cliff sits above 5x, not at
1x. At 22.5x the three rounds refuse 221059 increments and 39642 deletions over 135000
churned keys, and the filter comes back holding 31 of its 200 sentinels:

```text
  -- 84.5% false negatives, which a plain Bloom cannot produce at all. The
```

The false-positive rate goes the other way at the same time: 0.16275 for the counter
filter against 1.00000 for the bit-array twin that still holds all 171200 keys it was ever
given. So deletion does drain, and up to about the design load that exactness is worth the
4x memory; past saturation the failure mode is silent loss of live keys, not a slower
answer. Note `predicted` here is computed from occupancy, not from an insert counter —
after deletions those two numbers are different objects, and only the occupancy one tracks
the measurement.

### 7. What not knowing the capacity costs

The stream turns out to be 40000 keys; `first layer` is what the filter was told at
construction and it grows a layer whenever a layer fills; `known` is the filter sized for
the true count.

```text
  first layer    layers     bytes  vs known     budget   measured  probes   wasted
  40000 known         1     47926       1.0x    0.01004    0.01085       7       0%
  20000 keys          2     89923       1.9x    0.00505    0.00540      16      33%
  10000 keys          3    114528       2.4x    0.00753    0.00875      24      43%
  4000 keys           4    107236       2.2x    0.00886    0.00945      32      33%
  4000 decay=1        4     71891       1.5x    0.02986    0.03200      28      33%
```

Starting at 4000 keys — 10% of the truth — ends at 4 layers and 107236 bytes against the
known-size filter's 47926: 2.2x the memory and 32 slot checks per query against 7, while
the measured rate (0.00945) still lands under the 1% asked for. `wasted` is the other half:
the last layer is paid for in full and used only in part, and that unused room is what makes
the next layer affordable when it arrives.

`decay` is the knob that sets each layer's budget. With `decay=1.0` every layer is handed
the full 1% instead of a shrinking share: 71891 bytes — 1.5x cheaper than the `decay=2.0`
growth — at a measured 0.03200 against 0.00945. `fp_bound` (the union of the layer budgets)
tracks the measurement on both rows, which is the point of it: the budgets only sum to the
target when each layer is handed a smaller one than the last.

## How it decides

- **Sizing** — `optimal_m = ceil(-n ln p / (ln 2)^2)`, `optimal_k = round((m/n) ln 2)`,
  `predicted_fp = (1 - e^(-kn/m))^k`, `estimated_n = -(m/k) ln(1 - X/m)`. All four are
  functions in `bloomlib/estimate.py` and all four are checked in Sections 1–3.
- **`Bloom`** — one bit array, `m` bits MSB-first, `k` positions set per `add`; a query is
  `all(bits[i])`, so it can never answer False for a key it holds.
- **Positions** — `double` uses `h1 + i*h2 mod m` (one 64-bit digest, two halves);
  `splitmix` expands one digest through splitmix64; `independent` re-salts `blake2b` per
  index, which is what the closed form assumes and what Section 5 prices.
- **`CountingBloom`** — the same `k` positions, but a 4-bit nibble each; `add` increments,
  `remove` decrements *only if every position is > 0* (otherwise the deletion is refused,
  which is what `refused` counts), and an increment onto 15 is dropped and tallied in
  `saturated`. `predicted_fp` here is `(X/m)^k` on occupancy, not on `inserted`, because
  after deletions those are different quantities.
- **`DynamicBloom`** — layer `i` gets capacity `first * growth^i` and target
  `p / decay^(i+1)` with a distinct salt; a new layer opens when the current one reports
  `inserted == capacity`; a query checks every layer, hence `probes() == k * layers`.

## Limitations

- Python-level slot access: ~14–42 µs per 2000-key probe batch (Section 4) is a `get` per
  bit, not a word compare. A C or `bitarray`-backed version would be orders faster and the
  ratios here would carry, the absolutes would not.
- `estimated_n` has no signal once the array is full — it raises rather than returning a
  confident wrong number.
- `CountingBloom.remove` cannot know which key contributed to a saturated slot; Section 6
  is the measurement of what that costs, not a mitigation.
- `DynamicBloom` never shrinks a layer and never rehashes: `unused_capacity` is permanent
  until you rebuild with a known size.
- Sections 4 and 5 timings come from one machine on one run; the file says so and prints
  the spread. The count/byte/rate columns are seeded (fixed `keys()`/`disjoint()` seeds) and
  reproduce exactly.

## API

| Name | Signature | Notes |
|---|---|---|
| `Bloom` | `(capacity, false_positive=0.01, m=None, k=None, scheme="double", salt=b"bloom")` | `add` `update` `in` `positions` `bits_set` `load` `predicted_fp` `estimated_inserted` `bytes_used` `reset` |
| `CountingBloom` | `(capacity, false_positive=0.01, m=None, k=None, scheme="double", salt=b"counting")` | adds `remove` `remove_all` `counters_set` `saturated` |
| `DynamicBloom` | `(capacity, false_positive=0.01, growth=2, decay=2.0, scheme="double")` | `layers` `probes` `fp_bound` `total_capacity` `unused_capacity` |
| `optimal_m` / `optimal_k` / `predicted_fp` / `estimated_n` / `bits_per_item` | see above | the closed form |
| `digest64`, `double_positions`, `splitmix_positions`, `independent_positions`, `POSITIONS` | `(item, m, k, salt)` | position generators |
| `keys` / `disjoint` | `(count, ...)` | seeded key sets so runs reproduce |
| `BitArray` / `CounterArray` | `(size)` | MSB-first bit words; packed 4-bit counters that saturate at 15 |

`capacity` must be positive, `false_positive` inside `(0, 1)`, `growth >= 2`,
`decay >= 1.0`; `m >= 8` and `k >= 1` are checked at construction.

## Directory tree

```tree
bloom-filter-tuning/
├── bloomlib/
│   ├── __init__.py        # public exports, __version__
│   ├── estimate.py        # optimal_m / optimal_k / predicted_fp / estimated_n
│   ├── bits.py            # BitArray (packed bits) + CounterArray (packed nibbles)
│   ├── hashing.py         # digest64 + the three position schemes
│   ├── bloom.py           # Bloom
│   ├── counting.py        # CountingBloom (remove, saturation counter)
│   ├── dynamic.py         # DynamicBloom (layers, decay, fp_bound)
│   └── keys.py            # seeded key sets for the benchmark
├── tests/
│   └── test_bloom.py      # 51 unittest cases
├── benchmarks/
│   ├── run_benchmarks.py  # 7 measured sections, tee-d to last_run.txt
│   └── last_run.txt       # verbatim output of one run
├── LICENSE
├── pyproject.toml
├── .gitignore
├── .gitattributes
└── README.md
```

## Reproduce

```bash
# from the repository root
python -u -m unittest discover -s tests -p 'test_*.py' -t tests
python -u benchmarks/run_benchmarks.py
```

The benchmark prints to stdout and writes the identical text to
`benchmarks/last_run.txt`, so the numbers above cannot silently drift from the code; it
takes a few minutes because the saturation section churns 135000 keys. Re-run it and the
rate/byte/count columns stay put — only the `ns` columns move, and the file prints how far.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 sparkx313.
