"""Voice Studio core: designing, speaking with a clip, and clip processing (no real models)."""

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from this_voice_thing.core import voice_studio  # noqa: E402

SR = 24000


def tone(seconds, sr=SR, silence=0.0):
    t = np.arange(int(seconds * sr)) / sr
    voiced = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    pad = np.zeros(int(silence * sr), dtype=np.float32)
    return np.concatenate([pad, voiced, pad])


class FakeDesignModel:
    """Like the Qwen/VoxCPM/OmniVoice design facades: the first section of a run is
    designed from `instruct` unless an anchor is set, then everything clones from it."""

    backend, mode, sr = "qwen3", "voice_design", SR

    def __init__(self):
        self.instruct, self.ref_text = "kept description", ""
        self.locked_anchor, self._anchor = ("kept.wav", "kept text"), None
        self.calls = []

    def begin_run(self):
        self._anchor = self.locked_anchor

    def generate(self, text, **kwargs):
        self.calls.append(("design" if self._anchor is None else "clone", self.instruct, self._anchor, text))
        return np.zeros((1, SR), dtype=np.float32)


class FakeCloneModel(FakeDesignModel):
    backend, mode = "voxcpm", "base"

    def generate(self, text, audio_prompt_path=None, **kwargs):
        self.calls.append(("clone", self.instruct, audio_prompt_path, self.ref_text, kwargs))
        return np.zeros((1, SR), dtype=np.float32)


class FakeChatterbox:
    sr = SR

    def __init__(self):
        self.calls = []

    def generate(self, text, audio_prompt_path=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8):
        self.calls.append((text, audio_prompt_path, exaggeration, cfg_weight))  # no language_id: legacy
        return np.zeros((1, SR), dtype=np.float32)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.clip = voice_studio.save_take(self.dir, "clip", tone(2.0), SR)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_design_makes_a_fresh_voice_and_restores_the_model(self):
        model = FakeDesignModel()
        _wav, sr, seed = voice_studio.design(model, "a gruff old sailor")
        self.assertEqual(model.calls[-1][:3], ("design", "a gruff old sailor", None))
        self.assertEqual((model.instruct, model.locked_anchor), ("kept description", ("kept.wav", "kept text")))
        self.assertEqual(sr, SR)
        self.assertTrue(seed > 0)

    def test_design_needs_a_design_model_and_a_description(self):
        with self.assertRaises(ValueError):
            voice_studio.design(FakeCloneModel(), "anything")
        with self.assertRaises(ValueError):
            voice_studio.design(FakeDesignModel(), "  ")

    def test_speak_with_a_design_model_clones_from_the_clip(self):
        model = FakeDesignModel()
        voice_studio.speak(model, "Hello.", self.clip, "what it says")
        self.assertEqual(model.calls[-1][0], "clone")
        self.assertEqual(model.calls[-1][2], (self.clip, "what it says"))
        self.assertEqual(model.locked_anchor, ("kept.wav", "kept text"))

    def test_speak_with_styled_cloning(self):
        model = FakeCloneModel()
        voice_studio.speak(model, "Hello.", self.clip, "words", style="older, slower")
        kind, style, clip, transcript, _kwargs = model.calls[-1]
        self.assertEqual((kind, style, clip, transcript), ("clone", "older, slower", self.clip, "words"))
        self.assertEqual(model.instruct, "kept description")

    def test_speak_with_chatterbox_falls_back_without_language(self):
        model = FakeChatterbox()
        voice_studio.speak(model, "Hello.", self.clip, delivery={"exaggeration": 0.9, "cfg_weight": 0.3})
        self.assertEqual(model.calls[-1], ("Hello.", self.clip, 0.9, 0.3))

    def test_speak_needs_a_clip(self):
        with self.assertRaises(ValueError):
            voice_studio.speak(FakeChatterbox(), "Hello.", os.path.join(self.dir, "missing.wav"))

    def test_process_clip_cuts_trims_and_levels(self):
        wav = tone(2.0, silence=1.0)  # 1 s silence, 2 s tone, 1 s silence
        cut = voice_studio.process_clip(wav, SR, start=0.5, end=3.5)
        self.assertAlmostEqual(len(cut) / SR, 3.0, places=2)
        trimmed = voice_studio.process_clip(wav, SR, trim=True)
        self.assertLess(len(trimmed) / SR, 2.5)
        quiet = voice_studio.process_clip(tone(2.0) * 0.05, SR, level=True)
        self.assertGreater(np.sqrt(np.mean(quiet ** 2)), 0.05)

    def test_process_clip_refuses_a_tiny_selection(self):
        with self.assertRaises(ValueError):
            voice_studio.process_clip(tone(2.0), SR, start=1.0, end=1.2)

    def test_save_take_never_overwrites(self):
        second = voice_studio.save_take(self.dir, "clip", tone(1.0), SR)
        self.assertNotEqual(second, self.clip)
        self.assertTrue(os.path.exists(self.clip) and os.path.exists(second))


if __name__ == "__main__":
    unittest.main()
