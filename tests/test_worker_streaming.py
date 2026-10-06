import io
import threading
import unittest

from this_voice_thing.engines.worker import WorkerProcess


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()

    def poll(self):
        return None


def fake_worker(events):
    worker = WorkerProcess.__new__(WorkerProcess)
    worker.tag = "fake"
    worker.lock = threading.Lock()
    worker.process = FakeProcess()
    iterator = iter(events)
    worker._read_reply = lambda: next(iterator)
    return worker


class WorkerStreamingTests(unittest.TestCase):
    def test_stream_yields_events_until_done(self):
        worker = fake_worker([
            {"ok": True, "event": "start"},
            {"ok": True, "event": "audio", "data": "AA=="},
            {"ok": True, "event": "done"},
            {"ok": True, "event": "should-not-be-read"},
        ])

        events = list(worker.request_stream(cmd="generate_stream", text="hello"))

        self.assertEqual([event["event"] for event in events], ["start", "audio", "done"])
        self.assertIn('"cmd": "generate_stream"', worker.process.stdin.getvalue())
        self.assertTrue(worker.lock.acquire(blocking=False))
        worker.lock.release()

    def test_stream_error_releases_worker_lock(self):
        worker = fake_worker([
            {"ok": True, "event": "start"},
            {"ok": False, "event": "error", "error": "boom"},
        ])

        with self.assertRaisesRegex(RuntimeError, "boom"):
            list(worker.request_stream(cmd="generate_stream"))

        self.assertTrue(worker.lock.acquire(blocking=False))
        worker.lock.release()

    def test_closing_stream_releases_worker_lock(self):
        worker = fake_worker([
            {"ok": True, "event": "start"},
            {"ok": True, "event": "audio", "data": "AA=="},
            {"ok": True, "event": "done"},
        ])

        stream = worker.request_stream(cmd="generate_stream")
        self.assertEqual(next(stream)["event"], "start")
        stream.close()

        self.assertTrue(worker.lock.acquire(blocking=False))
        worker.lock.release()


if __name__ == "__main__":
    unittest.main()
