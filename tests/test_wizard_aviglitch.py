import unittest

import wizard


class WizardAviGlitchCommandTests(unittest.TestCase):
    def test_bloom_manual_pivot_command(self):
        cfg = {
            "ag_effect": "bloom",
            "pivot_frame": 42,
            "repeat_count": 20,
            "kill_ratio": 0.9,
            "ag_keep_audio": True,
        }
        cmd = wizard.build_command("aviglitch_mosh", ["input.mp4"], "out.mp4", cfg)

        self.assertIn("--effect", cmd)
        self.assertIn("bloom", cmd)
        self.assertIn("--pivot-frame", cmd)
        self.assertIn("42", cmd)
        self.assertNotIn("--pick-pivot", cmd)
        self.assertIn("--repeat-count", cmd)
        self.assertIn("--kill-ratio", cmd)
        self.assertIn("--keep-audio", cmd)

    def test_classic_command_ignores_bloom_fields(self):
        cfg = {
            "ag_effect": "classic",
            "drop_start": 2.0,
            "drop_end": 4.0,
            "dup_at": 3.0,
            "dup_count": 12,
            "pivot_frame": 42,
            "repeat_count": 20,
            "kill_ratio": 0.9,
        }
        cmd = wizard.build_command("aviglitch_mosh", ["input.mp4"], "out.mp4", cfg)

        self.assertIn("--drop-start", cmd)
        self.assertIn("--drop-end", cmd)
        self.assertIn("--dup-at", cmd)
        self.assertIn("--dup-count", cmd)
        self.assertNotIn("--pivot-frame", cmd)
        self.assertNotIn("--pick-pivot", cmd)


if __name__ == "__main__":
    unittest.main()
