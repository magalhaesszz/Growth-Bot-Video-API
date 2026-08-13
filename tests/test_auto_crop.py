import sys
import types
import unittest


config_stub = types.ModuleType("core.config")
config_stub.DEFAULT_CONFIG = {
    "speed_range": [1.02, 1.05], "brightness_range": [0.01, 0.02],
    "saturation_range": [-0.02, 0.02], "zoom_range": [1.01, 1.03],
    "flip_chance": 0.5,
}
config_stub.TIMEOUT_FFMPEG = 300
sys.modules.setdefault("core.config", config_stub)

from video.processor import _select_stable_crop


class AutoCropTests(unittest.TestCase):
    def test_selects_repeated_letterbox_crop(self):
        output = "\n".join([
            "[Parsed_cropdetect] crop=1920:800:0:140",
            "[Parsed_cropdetect] crop=1920:800:0:140",
            "[Parsed_cropdetect] crop=1920:800:0:140",
        ])
        self.assertEqual(_select_stable_crop(output, 1920, 1080), (1920, 800, 0, 140))

    def test_rejects_single_scene_candidate(self):
        output = "[Parsed_cropdetect] crop=1000:500:40:40"
        self.assertIsNone(_select_stable_crop(output, 1080, 600))

    def test_rejects_destructive_crop(self):
        output = "\n".join(["crop=100:100:0:0"] * 4)
        self.assertIsNone(_select_stable_crop(output, 1920, 1080))

    def test_ignores_full_frame(self):
        output = "\n".join(["crop=1920:1080:0:0"] * 4)
        self.assertIsNone(_select_stable_crop(output, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
