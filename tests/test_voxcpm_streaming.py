import base64
import unittest

from this_voice_thing.engines.voxcpm import VoxCPMModel


class FakeWorker:
    device = "cuda"

    def __init__(self):
        self.request = None

    def request_stream(self, **payload):
        self.request = payload
        yield {"ok": True, "event": "start", "sample_rate": 48000}
        yield {"ok": True, "event": "audio", "data": base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")}
        yield {"ok": True, "event": "done", "samples": 2, "chunks": 1}


class VoxCPMStreamingAdapterTests(unittest.TestCase):
    def test_native_stream_decodes_worker_audio(self):
        worker = FakeWorker()
        model = VoxCPMModel("openbmb/VoxCPM2", worker, 48000, "clone", v2=True)

        chunks = list(model.generate_streaming_pcm("Hello."))

        self.assertEqual(chunks, [b"\x01\x02\x03\x04"])
        self.assertEqual(worker.request["cmd"], "generate_stream")
        self.assertEqual(worker.request["text"], "Hello.")
        self.assertEqual(model.last_stream_metrics["chunks"], 1)


if __name__ == "__main__":
    unittest.main()
