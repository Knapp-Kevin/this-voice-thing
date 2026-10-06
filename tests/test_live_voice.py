import unittest

import numpy as np

from this_voice_thing.core.live_voice import LiveSpeechSession


class NativeModel:
    sr = 48000
    native_streaming = True

    def generate_streaming_pcm(self, text, audio_prompt_path=None):
        self.seen = (text, audio_prompt_path)
        yield b"\x01\x02"
        yield b"\x03\x04"


class SegmentedModel:
    sr = 24000
    segmented_streaming = True

    def generate_segmented_pcm(self, text, language_id=None, paragraph_pause=0.35):
        self.seen = (text, language_id, paragraph_pause)
        yield b"\x05\x06"


class BufferedModel:
    sr = 16000

    def generate(self, text, **kwargs):
        self.seen = (text, kwargs)
        return np.asarray([[0.5, -0.5]], dtype=np.float32)


class Pronunciations:
    def apply_section(self, text):
        return text.replace("SQL", "sequel"), 1


class LiveSpeechSessionTests(unittest.TestCase):
    def test_native_mode_yields_pcm_and_applies_pronunciation(self):
        model = NativeModel()
        session = LiveSpeechSession(
            model, "SQL works.", audio_prompt_path="voice.wav",
            pronunciations=Pronunciations(),
        )

        frames = list(session.frames())

        self.assertEqual(session.delivery_mode, "native")
        self.assertEqual([frame.pcm for frame in frames], [b"\x01\x02", b"\x03\x04"])
        self.assertEqual(model.seen, ("sequel works.", "voice.wav"))
        self.assertEqual(session.metrics()["frames"], 2)

    def test_segmented_mode_receives_language_and_pause(self):
        model = SegmentedModel()
        session = LiveSpeechSession(
            model, "Hello.", language_id="fr", paragraph_pause=0.6,
        )

        frames = list(session.frames())

        self.assertEqual(session.delivery_mode, "segmented")
        self.assertEqual(frames[0].pcm, b"\x05\x06")
        self.assertEqual(model.seen, ("Hello.", "fr", 0.6))

    def test_buffered_fallback_converts_float_waveform_to_s16(self):
        model = BufferedModel()
        session = LiveSpeechSession(model, "Hello.", generate_kwargs={"temperature": 0.7})

        frames = list(session.frames())
        samples = np.frombuffer(frames[0].pcm, dtype="<i2")

        self.assertEqual(session.delivery_mode, "buffered")
        self.assertEqual(len(samples), 2)
        self.assertGreater(samples[0], 16000)
        self.assertLess(samples[1], -16000)
        self.assertEqual(model.seen[0], "Hello.")

    def test_cancellation_closes_after_first_frame(self):
        model = NativeModel()
        session = LiveSpeechSession(model, "Hello.")
        checks = {"count": 0}

        def cancelled():
            checks["count"] += 1
            return checks["count"] > 1

        frames = list(session.frames(cancelled))

        self.assertEqual(len(frames), 1)
        self.assertTrue(session.cancelled)


if __name__ == "__main__":
    unittest.main()
