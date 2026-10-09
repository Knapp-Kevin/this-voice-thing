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


class _ResidueSink(_FakeSink):
    """A drained Windows sink: Idle, yet a few ms never reported free."""

    def __init__(self, device, fmt, parent):
        super().__init__(device, fmt, parent)
        self._state = None

    def state(self):
        from PySide6.QtMultimedia import QAudio
        return self._state if self._state is not None else QAudio.State.IdleState

    def bytesFree(self):
        return max(0, self._buffer_size - 1056)  # ~2.7 ms of 48 kHz stereo float


class LiveAudioOutputDrainTests(unittest.TestCase):
    def _output(self):
        output = LiveAudioOutput()
        with patch("this_voice_thing.ui.live_audio.QAudioSink", _ResidueSink):
            output.configure(_NegotiatingDevice({(48000, 2)}), 48000)
        output._started = True
        return output

    def test_drained_sink_with_residue_is_not_playing(self):
        output = self._output()
        output._input_finished = True
        self.assertGreater(output.buffered_ms(), 1.0)
        self.assertFalse(output.is_playing())
        output.stop()

    def test_active_sink_is_playing(self):
        from PySide6.QtMultimedia import QAudio
        output = self._output()
        output._input_finished = True
        output._sink._state = QAudio.State.ActiveState
        self.assertTrue(output.is_playing())
        output.stop()

    def test_mid_stream_underrun_still_counts_as_playing(self):
        output = self._output()
        output._input_finished = False  # generation still feeding
        self.assertTrue(output.is_playing())
        output.stop()


class _FastSession:
    """A source far faster than real time: 40 s of audio in four 10 s frames."""

    delivery_mode = "segmented"
    sample_rate = 24000
    provenance = "test"

    def frames(self, cancelled=lambda: False):
        from this_voice_thing.core.live_voice import AudioFrame
        for _ in range(4):
            if cancelled():
                return
            yield AudioFrame(pcm=b"\x00\x00" * self.sample_rate * 10, sample_rate=self.sample_rate)

    def metrics(self):
        return {"generation_seconds": 5.0, "audio_seconds": 40.0, "rtf": 0.125}


class LiveSpeechBackpressureTests(unittest.TestCase):
    """Regression: a fast source used to outrun playback, hit the 30 s pending cap and
    abort long utterances after about a second of audio."""

    def test_waits_while_output_is_far_ahead_and_resumes(self):
        import threading
        from this_voice_thing.ui.live_audio import LiveSpeechThread
        thread = LiveSpeechThread(_FastSession())
        thread.output_buffered_seconds = 20.0
        done = threading.Event()
        threading.Thread(target=lambda: (thread.wait_for_room(), done.set()), daemon=True).start()
        self.assertFalse(done.wait(0.15))      # held back
        thread.output_buffered_seconds = 2.0   # playback caught up
        self.assertTrue(done.wait(1.0))

    def test_stop_releases_a_waiting_generator(self):
        import threading
        from this_voice_thing.ui.live_audio import LiveSpeechThread
        thread = LiveSpeechThread(_FastSession())
        thread.output_buffered_seconds = 20.0
        done = threading.Event()
        threading.Thread(target=lambda: (thread.wait_for_room(), done.set()), daemon=True).start()
        thread.stop()
        self.assertTrue(done.wait(1.0))

    def test_fast_source_never_gets_more_than_the_limit_ahead(self):
        import threading
        from this_voice_thing.ui.live_audio import LiveSpeechThread
        thread = LiveSpeechThread(_FastSession())
        emitted, results = [], []
        thread.frame_ready.connect(lambda frame: emitted.append(len(frame.pcm)), Qt_direct())
        thread.session_complete.connect(results.append, Qt_direct())
        runner = threading.Thread(target=thread.run, daemon=True)
        runner.start()
        runner.join(0.5)
        # Nothing drains the "output": after the first frames it must hold back.
        ahead = sum(emitted) / (24000 * 2)
        self.assertLessEqual(ahead, LiveSpeechThread.MAX_AHEAD_SECONDS + 10.0)
        self.assertTrue(runner.is_alive())
        thread.output_buffered_seconds = 0.0  # playback "drains"; let it finish
        while runner.is_alive():
            thread.output_buffered_seconds = 0.0
            runner.join(0.05)
        self.assertEqual(sum(emitted) / (24000 * 2), 40.0)
        self.assertIn("backpressure_seconds", results[0])
        self.assertLess(results[0]["generation_seconds"], 5.0)


def Qt_direct():
    from PySide6.QtCore import Qt
    return Qt.ConnectionType.DirectConnection
