import unittest

from PySide6.QtMultimedia import QAudioFormat

from this_voice_thing.core.microphone_audio import MicrophoneEffectsConfig
from this_voice_thing.ui.live_capture import LiveAudioInput


class _FakeInputDevice:
    def __init__(self, supported=True):
        self._supported = bool(supported)
        self._preferred = QAudioFormat()
        self._preferred.setSampleRate(44100)
        self._preferred.setChannelCount(2)
        self._preferred.setSampleFormat(QAudioFormat.SampleFormat.Int16)

    def id(self):
        return b"fake-input"

    def description(self):
        return "Fake microphone"

    def preferredFormat(self):
        return self._preferred

    def isFormatSupported(self, fmt):
        if not self._supported:
            return False
        return (
            fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16
            and fmt.sampleRate() in (48000, 44100, 16000)
            and fmt.channelCount() in (1, 2)
        )


class LiveAudioInputConfigurationTests(unittest.TestCase):
    def test_configure_prefers_48k_mono_and_reports_passthrough(self):
        source = LiveAudioInput()
        meta = source.configure(
            _FakeInputDevice(),
            MicrophoneEffectsConfig(limiter_ceiling_db=0.0),
        )

        self.assertEqual(meta["sample_rate"], 48000)
        self.assertEqual(meta["channels"], 1)
        self.assertEqual(meta["device"], "Fake microphone")
        self.assertEqual(meta["provenance"], "microphone-passthrough")
        self.assertFalse(source.is_active())

    def test_configure_reports_dsp_provenance_when_effects_are_active(self):
        source = LiveAudioInput()
        meta = source.configure(
            _FakeInputDevice(),
            MicrophoneEffectsConfig(
                gain_db=3.0,
                limiter_ceiling_db=-1.0,
            ),
        )

        self.assertEqual(meta["provenance"], "microphone-dsp")
        self.assertEqual(source.stats()["effects"]["gain_db"], 3.0)

    def test_configure_rejects_missing_device(self):
        source = LiveAudioInput()

        with self.assertRaisesRegex(RuntimeError, "Choose a microphone"):
            source.configure(None)

    def test_configure_rejects_device_without_compatible_int16_format(self):
        source = LiveAudioInput()

        with self.assertRaisesRegex(RuntimeError, "compatible mono/stereo Int16"):
            source.configure(_FakeInputDevice(supported=False))


if __name__ == "__main__":
    unittest.main()
