import threading
import time
import unittest

import numpy as np

from this_voice_thing.core.microphone_audio import (
    MicrophoneBlockProcessor,
    MicrophoneEffectsConfig,
    MicrophoneEffectsProcessor,
    PcmRingBuffer,
    downmix_s16le,
)


class PcmRingBufferTests(unittest.TestCase):
    def test_overflow_drops_oldest_chunks_and_never_exceeds_bound(self):
        ring = PcmRingBuffer(8)

        ring.push(b"aaaa")
        ring.push(b"bbbb")
        ring.push(b"cccc")

        self.assertEqual(ring.pop(), b"bbbb")
        self.assertEqual(ring.pop(), b"cccc")
        stats = ring.stats()
        self.assertEqual(stats["pending_bytes"], 0)
        self.assertEqual(stats["dropped_bytes"], 4)
        self.assertEqual(stats["max_bytes"], 8)

    def test_close_wakes_waiting_reader_without_persisting_audio(self):
        ring = PcmRingBuffer(64)
        result = []

        def reader():
            result.append(ring.pop(timeout=1.0))

        thread = threading.Thread(target=reader)
        thread.start()
        time.sleep(0.02)
        ring.close(discard=True)
        thread.join(timeout=0.5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [None])
        self.assertEqual(ring.stats()["pending_bytes"], 0)


class DownmixTests(unittest.TestCase):
    def test_stereo_downmix_averages_channels_without_overflow(self):
        stereo = np.asarray(
            [[30000, 30000], [30000, -30000], [-20000, -10000]],
            dtype="<i2",
        ).reshape(-1)

        mono = np.frombuffer(downmix_s16le(stereo.tobytes(), 2), dtype="<i2")

        np.testing.assert_array_equal(
            mono,
            np.asarray([30000, 0, -15000], dtype="<i2"),
        )


class MicrophoneEffectsProcessorTests(unittest.TestCase):
    def test_neutral_config_preserves_pcm_exactly(self):
        pcm = np.asarray([0, 1000, -1000, 20000, -20000], dtype="<i2").tobytes()
        processor = MicrophoneEffectsProcessor(
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        )

        self.assertEqual(processor.process(pcm, 48000), pcm)

    def test_gain_changes_output_level_and_limiter_bounds_peak(self):
        pcm = np.asarray([4000, -4000, 16000, -16000], dtype="<i2").tobytes()
        processor = MicrophoneEffectsProcessor(
            MicrophoneEffectsConfig(gain_db=12.0, limiter_ceiling_db=-6.0)
        )

        result = np.frombuffer(processor.process(pcm, 48000), dtype="<i2")

        self.assertGreater(abs(int(result[0])), 4000)
        ceiling = int(32767 * (10.0 ** (-6.0 / 20.0))) + 2
        self.assertLessEqual(int(np.max(np.abs(result.astype(np.int32)))), ceiling)

    def test_compressor_reduces_loud_peak_relative_to_bypass(self):
        pcm = np.asarray([1000, 28000], dtype="<i2").tobytes()
        bypass = MicrophoneEffectsProcessor(
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        )
        compressed = MicrophoneEffectsProcessor(
            MicrophoneEffectsConfig(
                compressor_enabled=True,
                compressor_threshold_db=-18.0,
                compressor_ratio=4.0,
                limiter_ceiling_db=0.0,
            )
        )

        dry = np.frombuffer(bypass.process(pcm, 48000), dtype="<i2")
        wet = np.frombuffer(compressed.process(pcm, 48000), dtype="<i2")

        self.assertEqual(int(dry[0]), 1000)
        self.assertLess(abs(int(wet[1])), abs(int(dry[1])))

    def test_negative_tone_reduces_high_frequency_alternation(self):
        samples = np.asarray([12000, -12000] * 200, dtype="<i2")
        processor = MicrophoneEffectsProcessor(
            MicrophoneEffectsConfig(tone=-1.0, limiter_ceiling_db=0.0)
        )

        result = np.frombuffer(
            processor.process(samples.tobytes(), 48000),
            dtype="<i2",
        ).astype(np.int32)

        self.assertLess(np.mean(np.abs(np.diff(result))), 12000)


class MicrophoneBlockProcessorTests(unittest.TestCase):
    def test_preserves_partial_stereo_frame_between_blocks(self):
        processor = MicrophoneBlockProcessor(
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        )
        full = np.asarray([[1000, 3000], [-1000, -3000]], dtype="<i2").reshape(-1).tobytes()

        first = processor.process(full[:3], sample_rate=48000, channels=2)
        second = processor.process(full[3:], sample_rate=48000, channels=2)

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        np.testing.assert_array_equal(
            np.frombuffer(second.pcm, dtype="<i2"),
            np.asarray([2000, -2000], dtype="<i2"),
        )
        self.assertEqual(processor.metrics()["partial_frame_bytes"], 0)

    def test_partial_pcm_frame_is_carried_into_next_block(self):
        processor = MicrophoneBlockProcessor(
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        )
        stereo = np.asarray([[1000, 3000], [-1000, -3000]], dtype="<i2").reshape(-1).tobytes()

        first = processor.process(
            stereo[:3],
            sample_rate=48000,
            channels=2,
        )
        second = processor.process(
            stereo[3:],
            sample_rate=48000,
            channels=2,
        )

        self.assertIsNone(first)
        self.assertIsNotNone(second)
        np.testing.assert_array_equal(
            np.frombuffer(second.pcm, dtype="<i2"),
            np.asarray([2000, -2000], dtype="<i2"),
        )
        self.assertEqual(processor.metrics()["partial_frame_bytes"], 0)

    def test_discontinuity_clears_partial_frame_and_effect_state(self):
        processor = MicrophoneBlockProcessor(
            MicrophoneEffectsConfig(tone=-0.5, limiter_ceiling_db=0.0)
        )
        stereo = np.asarray([[1000, 3000]], dtype="<i2").reshape(-1).tobytes()

        self.assertIsNone(
            processor.process(stereo[:3], sample_rate=48000, channels=2)
        )
        self.assertEqual(processor.metrics()["partial_frame_bytes"], 3)

        processor.reset_discontinuity()

        self.assertEqual(processor.metrics()["partial_frame_bytes"], 0)
        self.assertEqual(processor.metrics()["discontinuities"], 1)

    def test_emits_shared_audio_frame_as_mono(self):
        stereo = np.asarray([[1000, 3000], [-1000, -3000]], dtype="<i2").reshape(-1)
        processor = MicrophoneBlockProcessor(
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0)
        )

        frame = processor.process(
            stereo.tobytes(),
            sample_rate=48000,
            channels=2,
        )

        self.assertEqual(frame.sample_rate, 48000)
        self.assertEqual(frame.channels, 1)
        self.assertEqual(frame.provenance, "microphone-passthrough")
        np.testing.assert_array_equal(
            np.frombuffer(frame.pcm, dtype="<i2"),
            np.asarray([2000, -2000], dtype="<i2"),
        )


if __name__ == "__main__":
    unittest.main()
