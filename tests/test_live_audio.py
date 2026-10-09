import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtMultimedia import QAudioFormat

from this_voice_thing.ui.live_audio import LiveAudioOutput, StreamingPcmConverter


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



class _SignalStub:
    def connect(self, _callback):
        return None


class _FakeSink:
    def __init__(self, _device, _fmt, _parent):
        self.stateChanged = _SignalStub()
        self._buffer_size = 0

    def setBufferSize(self, size):
        self._buffer_size = int(size)

    def bufferSize(self):
        return self._buffer_size

    def bytesFree(self):
        return self._buffer_size

    def reset(self):
        return None

    def deleteLater(self):
        return None


class _FakeDevice:
    def id(self):
        return b"fake-device"

    def description(self):
        return "Fake output"


class LiveAudioOutputLatencyTests(unittest.TestCase):
    def test_configure_accepts_low_latency_buffer_overrides(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        output = LiveAudioOutput()
        output._choose_format = lambda _device, _source_rate: fmt

        with patch("this_voice_thing.ui.live_audio.QAudioSink", _FakeSink):
            output.configure(
                _FakeDevice(),
                48000,
                start_buffer_seconds=0.02,
                sink_buffer_seconds=0.10,
            )

        stats = output.stats()
        self.assertEqual(stats["start_buffer_seconds"], 0.02)
        self.assertEqual(stats["sink_buffer_seconds"], 0.10)
        self.assertEqual(output._sink.bufferSize(), 9600)
        output.stop()


if __name__ == "__main__":
    unittest.main()


class _NegotiatingDevice(_FakeDevice):
    """A device that, like real Windows endpoints, only accepts some formats."""

    def __init__(self, supported):
        self.supported = supported  # {(rate, channels), ...}

    def preferredFormat(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return fmt

    def isFormatSupported(self, fmt):
        return (fmt.sampleRate(), fmt.channelCount()) in self.supported


class LiveAudioOutputFormatTests(unittest.TestCase):
    """configure() on a fresh output must negotiate a real format (regression: an
    instance attribute named _format shadowed the format-building method, so the
    first configure() on any real device raised TypeError)."""

    def test_first_configure_negotiates_without_stubs(self):
        output = LiveAudioOutput()
        with patch("this_voice_thing.ui.live_audio.QAudioSink", _FakeSink):
            output.configure(_NegotiatingDevice({(48000, 2)}), 24000)
        stats = output.stats()
        self.assertEqual((output._format.sampleRate(), output._format.channelCount()), (48000, 2))
        self.assertIn("48000", str(stats))
        output.stop()

    def test_reconfigure_after_stop_still_negotiates(self):
        output = LiveAudioOutput()
        device = _NegotiatingDevice({(44100, 1)})
        with patch("this_voice_thing.ui.live_audio.QAudioSink", _FakeSink):
            output.configure(device, 44100)
            output.stop()
            output.configure(device, 22050)
        self.assertEqual((output._format.sampleRate(), output._format.channelCount()), (44100, 1))
        output.stop()

    def test_unsupported_device_reports_clearly(self):
        output = LiveAudioOutput()
        with self.assertRaises(RuntimeError):
            output.configure(_NegotiatingDevice(set()), 24000)


class _FloatOnlyDevice(_NegotiatingDevice):
    """Like Qt's Windows shared-mode endpoints: only the float32 mix format works."""

    def __init__(self):
        super().__init__(set())

    def isFormatSupported(self, fmt):
        return (fmt.sampleFormat() == QAudioFormat.SampleFormat.Float
                and (fmt.sampleRate(), fmt.channelCount()) == (48000, 2))

    def preferredFormat(self):
        fmt = super().preferredFormat()
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        return fmt


class LiveAudioOutputFloatTests(unittest.TestCase):
    def test_float_only_device_gets_float_sink_and_float_samples(self):
        output = LiveAudioOutput()
        with patch("this_voice_thing.ui.live_audio.QAudioSink", _FakeSink):
            output.configure(_FloatOnlyDevice(), 48000)
            self.assertEqual(output._format.sampleFormat(), QAudioFormat.SampleFormat.Float)
            self.assertEqual(output.stats()["sample_format"], "f32")
            self.assertIn("f32", output.route_description())
            pcm = np.array([16384, -16384, 0, 32767], dtype="<i2").tobytes()
            output.push(pcm)
            samples = np.frombuffer(bytes(output._pending), dtype="<f4")
        # mono -> stereo duplicates; s16 -> float scales by 1/32768
        np.testing.assert_allclose(samples[:4], [0.5, 0.5, -0.5, -0.5], atol=1e-4)
        self.assertEqual(output._bytes_per_second(), 48000 * 2 * 4)
        output.stop()
