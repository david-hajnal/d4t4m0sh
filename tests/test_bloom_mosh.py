import unittest

from aviglitch_mosh import build_bloom_sequence


class BloomMoshSequenceTests(unittest.TestCase):
    def test_inserts_exact_repeat_count(self):
        frames = ["f0", "f1", "f2", "f3"]
        result, pivot, repeat = build_bloom_sequence(frames, pivot_frame=2, repeat_count=3)

        self.assertEqual(pivot, 2)
        self.assertEqual(repeat, 3)
        self.assertEqual(len(result), len(frames) + 3)
        self.assertEqual(result, ["f0", "f1", "f2", "f2", "f2", "f2", "f3"])

    def test_frame_order_before_and_after_pivot(self):
        frames = ["a", "b", "c", "d", "e"]
        result, pivot, repeat = build_bloom_sequence(frames, pivot_frame=3, repeat_count=2)

        self.assertEqual(result[:pivot], frames[:pivot])
        suffix_start = pivot + repeat
        self.assertEqual(result[suffix_start:], frames[pivot:])

    def test_invalid_pivot_and_repeat_are_safe(self):
        frames = ["x", "y", "z"]
        result, pivot, repeat = build_bloom_sequence(frames, pivot_frame=99, repeat_count=-5)

        self.assertEqual(pivot, 2)
        self.assertEqual(repeat, 0)
        self.assertEqual(result, frames)

        result2, pivot2, repeat2 = build_bloom_sequence(frames, pivot_frame=-4, repeat_count="nope")
        self.assertEqual(pivot2, 0)
        self.assertEqual(repeat2, 0)
        self.assertEqual(result2, frames)


if __name__ == "__main__":
    unittest.main()
