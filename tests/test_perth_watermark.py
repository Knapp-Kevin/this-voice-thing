"""The Perth watermark wrapper must run the network with autograd off (regression:
without it every call leaked ~16 MB per 8 s of audio, growing Live Voice and Generate
memory without bound)."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from this_voice_thing.engines.worker import PerthWatermark  # noqa: E402


class _RecordingWatermarker:
    def __init__(self):
        self.modes = []

    def apply_watermark(self, wav, sample_rate):
        import torch
        self.modes.append((torch.is_grad_enabled(), torch.is_inference_mode_enabled()))
        return np.asarray(wav) * 0.5


class PerthWatermarkTests(unittest.TestCase):
    def test_runs_without_autograd(self):
        watermark = PerthWatermark()
        watermark._watermarker = _RecordingWatermarker()
        out = watermark.apply(np.ones(4, dtype=np.float32), 24000, "Test")
        self.assertEqual(watermark._watermarker.modes, [(False, True)])
        np.testing.assert_allclose(out, 0.5)
        self.assertEqual(out.dtype, np.float32)

    def test_failure_returns_the_original_audio(self):
        class Broken:
            def apply_watermark(self, wav, sample_rate):
                raise RuntimeError("boom")
        watermark = PerthWatermark()
        watermark._watermarker = Broken()
        wav = np.ones(4, dtype=np.float32)
        self.assertIs(watermark.apply(wav, 24000, "Test"), wav)


if __name__ == "__main__":
    unittest.main()
