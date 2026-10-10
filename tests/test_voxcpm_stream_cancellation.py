import base64
import os
import unittest

from this_voice_thing.engines.voxcpm import VoxCPMModel


class CancellationAwareWorker:
    device = "cuda"

    def __init__(self):
        self.payload = None
        self.cancel_seen_on_close = None

    def request_stream(self, **payload):
        self.payload = payload
        try:
            yield {"ok": True, "event": "start", "sample_rate": 48000}
            yield {"ok": True, "event": "audio", "data": base64.b64encode(b"\x01\x02").decode("ascii")}
            yield {"ok": True, "event": "done", "samples": 1, "chunks": 1, "cancelled": False}
        finally:
            self.cancel_seen_on_close = os.path.exists(payload["cancel_path"])


class VoxCPMStreamCancellationTests(unittest.TestCase):
    def test_early_close_signals_cancel_before_worker_stream_closes(self):
        worker = CancellationAwareWorker()
        model = VoxCPMModel("openbmb/VoxCPM2", worker, 48000, "clone", v2=True)

        stream = model.generate_streaming_pcm("Please stop after the first chunk.")
        self.assertEqual(next(stream), b"\x01\x02")
        cancel_path = worker.payload["cancel_path"]
        self.assertFalse(os.path.exists(cancel_path))

        stream.close()

        self.assertTrue(worker.cancel_seen_on_close)
        self.assertFalse(os.path.exists(cancel_path))

    def test_completed_stream_does_not_signal_cancel(self):
        worker = CancellationAwareWorker()
        model = VoxCPMModel("openbmb/VoxCPM2", worker, 48000, "clone", v2=True)

        self.assertEqual(list(model.generate_streaming_pcm("Finish normally.")), [b"\x01\x02"])

        self.assertFalse(worker.cancel_seen_on_close)
        self.assertFalse(os.path.exists(worker.payload["cancel_path"]))
        self.assertFalse(model.last_stream_metrics["cancelled"])


if __name__ == "__main__":
    unittest.main()
