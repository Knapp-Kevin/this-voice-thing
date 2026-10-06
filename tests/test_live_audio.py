import unittest

import numpy as np

from this_voice_thing.ui.live_audio import StreamingPcmConverter


class StreamingPcmConverterTests(unittest.TestCase):
    def test_same_rate_preserves_mono_samples(self):
        samples = np.asarray([0, 1000, -1000, 32767], dtype="<i2")
        converter = StreamingPcmConverter(48000, 48000, 1)

        result = np.frombuffer(converter.convert(samples.tobytes()), dtype="<i2")

        np.testing.assert_array_equal(result, samples)

    def test_stereo_output_duplicates_channels(self):
        samples = np.asarray([100, -200], dtype="<i2")
        converter = StreamingPcmConverter(48000, 48000, 2)

        result = np.frombuffer(converter.convert(samples.tobytes()), dtype="<i2").reshape(-1, 2)

        np.testing.assert_array_equal(result[:, 0], samples)
        np.testing.assert_array_equal(result[:, 1], samples)

    def test_resampling_is_continuous_across_chunks(self):
        first = np.asarray([0, 1000, 2000], dtype="<i2")
        second = np.asarray([3000, 4000, 5000], dtype="<i2")
        converter = StreamingPcmConverter(24000, 48000, 1)

        a = np.frombuffer(converter.convert(first.tobytes()), dtype="<i2")
        b = np.frombuffer(converter.convert(second.tobytes()), dtype="<i2")
        tail = np.frombuffer(converter.convert(b"", final=True), dtype="<i2")
        result = np.concatenate((a, b, tail))

        self.assertGreater(len(result), len(first) + len(second))
        self.assertTrue(np.all(np.diff(result.astype(np.int32)) >= 0))
        self.assertLessEqual(abs(int(result[-1]) - 5000), 500)


if __name__ == "__main__":
    unittest.main()
