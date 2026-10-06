import unittest
from unittest.mock import patch

import numpy as np

from this_voice_thing.core.documents import Section
from this_voice_thing.engines.kokoro import KokoroModel


class FakeKokoro(KokoroModel):
    def __init__(self):
        self.sr = 1000
        self.segmented_streaming = True
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return np.asarray([[0.25, -0.25]], dtype=np.float32)


class KokoroSegmentedStreamingTests(unittest.TestCase):
    def test_yields_each_section_with_existing_seam_gap(self):
        model = FakeKokoro()
        sections = [
            Section("First sentence.", "sentence"),
            Section("Second sentence.", "end"),
        ]

        with patch("this_voice_thing.engines.kokoro.documents.plan_sections", return_value=sections), \
             patch("this_voice_thing.engines.kokoro.audio_effects.trim_silence",
                   side_effect=lambda wav, _sr: wav), \
             patch("this_voice_thing.engines.kokoro.audio_effects.seam_gap", return_value=0.1):
            chunks = list(model.generate_segmented_pcm(
                "First sentence. Second sentence.",
                language_id="en",
                paragraph_pause=0.4,
            ))

        self.assertEqual(len(chunks), 2)
        first = np.frombuffer(chunks[0], dtype="<i2")
        second = np.frombuffer(chunks[1], dtype="<i2")
        self.assertEqual(len(first), 102)  # 2 audio samples + 100 ms seam at 1 kHz
        self.assertTrue(np.all(first[2:] == 0))
        self.assertEqual(len(second), 2)
        self.assertEqual(model.calls[0][1]["language_id"], "en")
        self.assertEqual([text for text, _kwargs in model.calls],
                         ["First sentence.", "Second sentence."])


if __name__ == "__main__":
    unittest.main()
