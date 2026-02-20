import unittest

from tools.keyframe_helper_core import (
    VideoInfo,
    build_selection_payload,
    clamp_frame,
    find_nearest_keyframe_frame,
    keyframe_times_to_frames,
)


class KeyframeHelperCoreTests(unittest.TestCase):
    def test_clamp_frame(self):
        self.assertEqual(clamp_frame(-5, 100), 0)
        self.assertEqual(clamp_frame(0, 100), 0)
        self.assertEqual(clamp_frame(99, 100), 99)
        self.assertEqual(clamp_frame(1000, 100), 99)

    def test_keyframe_times_to_frames(self):
        info = VideoInfo(path="in.mp4", fps=24.0, frame_count=100, duration=4.0)
        frames = keyframe_times_to_frames([-1.0, 0.0, 0.5, 99.0], info)
        self.assertEqual(frames, [0, 12, 99])

    def test_find_nearest_keyframe_frame(self):
        keys = [0, 25, 50, 75]
        self.assertEqual(find_nearest_keyframe_frame(3, keys), 0)
        self.assertEqual(find_nearest_keyframe_frame(49, keys), 50)
        self.assertEqual(find_nearest_keyframe_frame(74, keys), 75)

    def test_payload_contains_selection_and_commands(self):
        info = VideoInfo(path="clip.mp4", fps=25.0, frame_count=250, duration=10.0)
        payload = build_selection_payload(
            video_info=info,
            selected_frame=100,
            keyframe_frames=[0, 50, 100, 150],
            repeat_count=20,
            kill_ratio=0.85,
        )
        self.assertEqual(payload["selection"]["selected_frame"], 100)
        self.assertIn("--pivot-frame 100", payload["suggested_commands"]["bloom_manual_pivot"])
        self.assertIn("--repeat-count 20", payload["suggested_commands"]["bloom_manual_pivot"])
        self.assertIn("--kill-ratio 0.850", payload["suggested_commands"]["bloom_manual_pivot"])


if __name__ == "__main__":
    unittest.main()
