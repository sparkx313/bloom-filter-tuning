"""bloomlib 的行为测试。

两类断言分得很清楚：结构性的（位数组打包、饱和、分层增长）逐条钉死；统计性的（误判率）
只断言「量级和方向」，并把样本标准差算进容差 —— 一个会因为随机种子而失败的测试不算测试。
"""

import math
import sys
import unittest

sys.path.insert(0, ".")

from bloomlib import (Bloom, CountingBloom, DynamicBloom, bits_per_item, digest64,
                      disjoint, double_positions, estimated_n, independent_positions,
                      keys, optimal_k, optimal_m, predicted_fp, splitmix_positions)
from bloomlib.bits import BitArray, CounterArray


class BitArrayTest(unittest.TestCase):
    def test_sets_and_reads_back(self):
        bits = BitArray(100)
        self.assertFalse(bits.get(37))
        bits.set(37)
        self.assertTrue(bits.get(37))
        self.assertFalse(bits.get(38))

    def test_set_reports_whether_it_changed(self):
        bits = BitArray(16)
        self.assertTrue(bits.set(5))
        self.assertFalse(bits.set(5))

    def test_packs_eight_bits_per_byte(self):
        self.assertEqual(len(BitArray(1).data), 1)
        self.assertEqual(len(BitArray(8).data), 1)
        self.assertEqual(len(BitArray(9).data), 2)
        self.assertEqual(BitArray(1000).bytes_used(), 125)

    def test_boundaries(self):
        bits = BitArray(64)
        for index in (0, 63):
            bits.set(index)
        self.assertEqual(bits.count(), 2)
        with self.assertRaises(IndexError):
            bits.set(64)

    def test_count_matches_python_bit_count(self):
        bits = BitArray(256)
        for index in range(0, 256, 7):
            bits.set(index)
        self.assertEqual(bits.count(), len(range(0, 256, 7)))

    def test_clear(self):
        bits = BitArray(64)
        bits.set(9)
        bits.clear()
        self.assertEqual(bits.count(), 0)

    def test_rejects_sizes_it_cannot_hold(self):
        with self.assertRaises(ValueError):
            BitArray(0)
        with self.assertRaises(ValueError):
            BitArray(10, bytearray(4))


class CounterArrayTest(unittest.TestCase):
    def test_packs_two_counters_per_byte(self):
        counters = CounterArray(10)
        self.assertEqual(len(counters.data), 5)
        counters._put(1, 9)
        counters._put(2, 4)
        self.assertEqual((counters.get(0), counters.get(1), counters.get(2)), (0, 9, 4))

    def test_saturates_instead_of_wrapping(self):
        counters = CounterArray(4)
        clipped = [counters.increment(0) for _ in range(20)]
        self.assertEqual(counters.get(0), 15)
        self.assertEqual(sum(clipped), 5)  # 前 15 次成功，后 5 次报饱和

    def test_decrement_stops_at_zero(self):
        counters = CounterArray(4)
        counters.increment(3)
        self.assertTrue(counters.decrement(3))
        self.assertFalse(counters.decrement(3))
        self.assertEqual(counters.get(3), 0)

    def test_only_four_bit_width_is_packed(self):
        with self.assertRaises(ValueError):
            CounterArray(8, width=8)


class HashingTest(unittest.TestCase):
    def test_digest_is_stable_and_sixty_four_bits(self):
        value = digest64("same key")
        self.assertEqual(value, digest64("same key"))
        self.assertLess(value, 1 << 64)
        self.assertNotEqual(value, digest64("same key", b"salt"))

    def test_accepts_text_and_bytes_alike(self):
        self.assertEqual(double_positions("abc", 1000, 4), double_positions(b"abc", 1000, 4))

    def test_rejects_other_types(self):
        for scheme in (double_positions, independent_positions, splitmix_positions):
            with self.assertRaises(TypeError):
                scheme(7, 1000, 4)

    def test_positions_stay_in_range(self):
        for scheme in (double_positions, independent_positions, splitmix_positions):
            for index in range(50):
                positions = scheme("key-%d" % index, 9973, 7)
                self.assertEqual(len(positions), 7)
                self.assertTrue(all(0 <= value < 9973 for value in positions))

    def test_splitmix_expands_distinctly(self):
        positions = splitmix_positions("one key", 1 << 20, 8)
        self.assertEqual(len(set(positions)), len(positions))

    def test_the_two_seeded_schemes_disagree(self):
        """同一批键、两种方案，落点几乎不该重合 —— 分层增长靠的就是这种独立性。"""
        overlap = sum(1 for index in range(200)
                      if set(double_positions("k%d" % index, 65536, 4))
                      & set(splitmix_positions("k%d" % index, 65536, 4)))
        self.assertLess(overlap, 5)


class EstimateTest(unittest.TestCase):
    def test_nine_and_a_half_bits_per_item_for_one_percent(self):
        m = optimal_m(1000, 0.01)
        self.assertAlmostEqual(bits_per_item(m, 1000), 9.585, delta=0.01)
        self.assertEqual(optimal_k(m, 1000), 7)

    def test_sizing_grows_with_a_tighter_target(self):
        self.assertGreater(optimal_m(1000, 0.001), optimal_m(1000, 0.01))
        self.assertLess(predicted_fp(10000, 7, 500), predicted_fp(10000, 7, 5000))

    def test_prediction_starts_at_zero(self):
        self.assertEqual(predicted_fp(1000, 7, 0), 0.0)
        self.assertAlmostEqual(predicted_fp(1000, 7, 1), (1.0 - math.exp(-7 / 1000.0)) ** 7)

    def test_rejects_sizes_that_make_no_sense(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                optimal_m(bad, 0.01)
        for bad in (0.0, 1.0, 1.5):
            with self.assertRaises(ValueError):
                optimal_m(100, bad)

    def test_occupancy_recovers_the_count(self):
        bloom = Bloom(2000, 0.01)
        inserted, probes = disjoint(2000, seed=11)
        bloom.update(inserted)
        self.assertAlmostEqual(estimated_n(bloom.m, bloom.k, bloom.bits_set()),
                               2000, delta=2000 * 0.05)

    def test_occupancy_refuses_a_full_array(self):
        with self.assertRaises(ValueError):
            estimated_n(64, 7, 64)


class BloomTest(unittest.TestCase):
    def setUp(self):
        self.inserted, self.probes = disjoint(4000, seed=2)
        self.bloom = Bloom(4000, 0.01)

    def test_no_false_negatives(self):
        self.bloom.update(self.inserted)
        missing = [key for key in self.inserted if key not in self.bloom]
        self.assertEqual(missing, [])

    def test_measured_rate_sits_on_the_prediction(self):
        self.bloom.update(self.inserted)
        hits = sum(1 for key in self.probes if key in self.bloom)
        measured = hits / float(len(self.probes))
        error = math.sqrt(max(measured, 1e-9) / float(len(self.probes)))
        self.assertLess(abs(measured - self.bloom.predicted_fp()), 4 * error + 1e-4)

    def test_an_empty_filter_answers_no(self):
        self.assertNotIn("never inserted", self.bloom)
        self.assertEqual(self.bloom.inserted, 0)

    def test_sizing_follows_the_target(self):
        tight = Bloom(4000, 0.0001)
        self.assertGreater(tight.m, self.bloom.m)
        self.assertGreater(tight.bytes_used(), self.bloom.bytes_used())

    def test_explicit_geometry_overrides_the_formulas(self):
        forced = Bloom(4000, m=8192, k=3)
        self.assertEqual((forced.m, forced.k), (8192, 3))

    def test_rehashing_the_same_key_does_not_move_it(self):
        self.bloom.add("steady")
        self.assertIn("steady", self.bloom)
        before = self.bloom.bits_set()
        self.bloom.add("steady")
        self.assertEqual(self.bloom.bits_set(), before)

    def test_load_rises_with_fill(self):
        empty = self.bloom.load()
        self.bloom.update(self.inserted)
        self.assertGreater(self.bloom.load(), empty)
        self.assertGreater(self.bloom.load(), 0.4)

    def test_reset_empties_the_bits(self):
        self.bloom.update(self.inserted)
        self.bloom.reset()
        self.assertEqual(self.bloom.bits_set(), 0)
        self.assertEqual(self.bloom.inserted, 0)
        self.assertNotIn(self.inserted[0], self.bloom)

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            Bloom(100, 0.01, m=4)
        with self.assertRaises(ValueError):
            Bloom(100, 0.01, k=0)
        with self.assertRaises(ValueError):
            Bloom(100, 0.01, scheme="murder")

    def test_two_salts_are_independent_filters(self):
        first = Bloom(500, 0.01, salt=b"a")
        second = Bloom(500, 0.01, salt=b"b")
        first.update(self.inserted[:500])
        hits = sum(1 for key in self.inserted[:500] if key in second)
        self.assertEqual(hits, 0)


class CountingBloomTest(unittest.TestCase):
    def setUp(self):
        self.inserted, self.probes = disjoint(1500, seed=4)
        self.filter = CountingBloom(1500, 0.01)
        self.filter.update(self.inserted)

    def test_removal_makes_items_disappear(self):
        removed = self.inserted[:300]
        for key in removed:
            self.assertTrue(self.filter.remove(key))
        still = sum(1 for key in removed if key in self.filter)
        self.assertLess(still, 300 * 0.05)

    def test_survivors_are_never_lost(self):
        for key in self.inserted[:300]:
            self.filter.remove(key)
        missing = [key for key in self.inserted[300:] if key not in self.filter]
        self.assertEqual(missing, [])

    def test_removing_something_absent_is_refused(self):
        self.assertFalse(self.filter.remove(self.probes[0]))
        self.assertEqual(self.filter.inserted, 1500)

    def test_no_false_negatives_before_any_removal(self):
        self.assertEqual([key for key in self.inserted if key not in self.filter], [])

    def test_counters_cost_four_times_the_bits(self):
        bits = Bloom(1500, 0.01)
        self.assertEqual(self.filter.bytes_used(), (self.filter.m + 1) // 2)
        self.assertGreater(self.filter.bytes_used(), 3 * bits.bytes_used())

    def test_saturation_is_counted_not_wrapped(self):
        hot = CountingBloom(200, 0.01)
        for _ in range(40):
            hot.add("hot key")
        self.assertEqual(hot.counters.get(hot.positions("hot key")[0]), 15)
        self.assertGreater(hot.saturated, 0)

    def test_remove_all_returns_the_count_it_actually_deleted(self):
        deleted = self.filter.remove_all(self.inserted)
        self.assertEqual(deleted, 1500)
        self.assertEqual(self.filter.inserted, 0)


class DynamicBloomTest(unittest.TestCase):
    def setUp(self):
        self.inserted, self.probes = disjoint(8000, seed=6)
        self.filter = DynamicBloom(1000, 0.01)
        self.filter.update(self.inserted)

    def test_layers_open_at_capacity(self):
        self.assertGreater(len(self.filter.layers), 3)
        sizes = [layer.capacity for layer in self.filter.layers]
        self.assertEqual(sizes, [1000 * 2 ** index for index in range(len(sizes))])

    def test_no_false_negatives_across_layers(self):
        self.assertEqual([key for key in self.inserted if key not in self.filter], [])

    def test_probes_grow_with_layers(self):
        self.assertEqual(self.filter.probes(), self.filter.k * len(self.filter.layers))

    def test_a_halved_budget_stays_under_the_target(self):
        self.assertLess(self.filter.fp_bound(), 0.01 * 1.01)
        measured = sum(1 for key in self.probes if key in self.filter) / float(len(self.probes))
        self.assertLess(measured, 0.02)

    def test_a_flat_budget_is_cheaper_and_sloppier(self):
        flat = DynamicBloom(1000, 0.01, decay=1.0)
        flat.update(self.inserted)
        self.assertLess(flat.bytes_used(), self.filter.bytes_used())
        self.assertGreater(flat.fp_bound(), self.filter.fp_bound())

    def test_layering_wastes_room_that_a_known_count_does_not(self):
        known = Bloom(8000, 0.01)
        known.update(self.inserted)
        self.assertGreater(self.filter.bytes_used(), known.bytes_used())

    def test_last_layer_holds_the_leftover_capacity(self):
        self.assertGreater(self.filter.unused_capacity(), 0.0)
        self.assertLess(self.filter.unused_capacity(), 1.0)

    def test_rejects_growth_that_cannot_gain_room(self):
        with self.assertRaises(ValueError):
            DynamicBloom(100, 0.01, growth=1)
        with self.assertRaises(ValueError):
            DynamicBloom(100, 0.01, decay=0.5)


class KeysTest(unittest.TestCase):
    def test_the_two_halves_never_touch(self):
        inserted, probes = disjoint(500, seed=8)
        self.assertEqual(len(inserted), 500)
        self.assertEqual(set(inserted) & set(probes), set())

    def test_a_seed_reproduces_the_stream(self):
        self.assertEqual(keys(50, "p", seed=9), keys(50, "p", seed=9))
        self.assertNotEqual(keys(50, "p", seed=9), keys(50, "p", seed=10))

    def test_prefixes_separate_the_streams(self):
        self.assertTrue(all(key.startswith("in/") for key in keys(10, "in")))


if __name__ == "__main__":
    unittest.main()
