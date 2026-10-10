"""VoxCPM2 Live Voice step presets: Live can ask for fewer diffusion steps without
changing what Generate (and the local API) use."""

import unittest

from this_voice_thing.core.live_voice import LiveSpeechSession
from this_voice_thing.engines import voxcpm


class _Worker:
    device = "cuda"

    def __init__(self):
        self.requests = []

    def request(self, **request):
        self.requests.append(request)
        raise RuntimeError("not generating in tests")

    def request_stream(self, **request):
        self.requests.append(request)
        return iter([{"event": "audio", "data": "AAA="}, {"event": "done"}])


def _model():
    return voxcpm.VoxCPMModel("openbmb/VoxCPM2", _Worker(), 48000, "clone")


class VoxCPMStepTests(unittest.TestCase):
    def test_presets_and_defaults(self):
        self.assertEqual({k: v[0] for k, v in voxcpm.LIVE_STEP_PRESETS.items()},
                         {"full": 10, "balanced": 8, "low_latency": 6})
        self.assertEqual(voxcpm.DEFAULT_LIVE_PRESET, "low_latency")  # Live only
        self.assertEqual(_model().timesteps, voxcpm.DEFAULT_TIMESTEPS)

    def test_generate_requests_keep_the_model_default(self):
        model = _model()
        request = model._generation_request("Hello.", None)
        self.assertEqual(request["timesteps"], 10)

    def test_stream_uses_the_override_only_for_that_stream(self):
        model = _model()
        list(model.generate_streaming_pcm("Hello.", timesteps=6))
        self.assertEqual(model.worker.requests[-1]["timesteps"], 6)
        self.assertEqual(model.last_stream_timesteps, 6)
        self.assertEqual(model.timesteps, 10)  # Generate's default untouched
        list(model.generate_streaming_pcm("Hello."))
        self.assertEqual(model.worker.requests[-1]["timesteps"], 10)


class _NativeModel:
    sr = 24000
    native_streaming = True

    def __init__(self):
        self.calls = []

    def generate_streaming_pcm(self, text, audio_prompt_path=None, **kwargs):
        self.calls.append(kwargs)
        yield b"\x00\x00" * 240


class LiveSessionStepTests(unittest.TestCase):
    def test_session_passes_steps_to_native_streaming(self):
        model = _NativeModel()
        session = LiveSpeechSession(model, "Hello.", native_timesteps=6)
        list(session.frames())
        self.assertEqual(model.calls, [{"timesteps": 6}])
        self.assertEqual(session.metrics()["native_timesteps"], 6)

    def test_session_without_steps_keeps_the_old_call(self):
        model = _NativeModel()
        session = LiveSpeechSession(model, "Hello.")
        list(session.frames())
        self.assertEqual(model.calls, [{}])
        self.assertIsNone(session.metrics()["native_timesteps"])


if __name__ == "__main__":
    unittest.main()
