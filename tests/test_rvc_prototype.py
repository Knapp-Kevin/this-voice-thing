import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np

from this_voice_thing.engines import rvc


class RVCRequirementsTests(unittest.TestCase):
    def test_sanitize_removes_mirrors_and_torch_family(self):
        source = """--index-url https://mirror.invalid/simple
--extra-index-url https://torch.invalid
torch==2.7.1+cu128
torchaudio==2.7.1+cu128
torchvision==0.22.1
numpy>=1.26,<2
librosa>=0.10,<0.11
"""
        cleaned = rvc.sanitize_upstream_requirements(source)

        self.assertNotIn("--index-url", cleaned)
        self.assertNotIn("--extra-index-url", cleaned)
        self.assertNotIn("torch==", cleaned)
        self.assertNotIn("torchaudio==", cleaned)
        self.assertNotIn("torchvision==", cleaned)
        self.assertIn("numpy>=1.26,<2", cleaned)
        self.assertIn("librosa>=0.10,<0.11", cleaned)

    def test_required_upstream_files_are_commit_specific_runtime_surface(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = rvc.required_upstream_files(temp)

        relative = [str(path).replace("\\", "/") for path in paths]
        self.assertTrue(any(path.endswith("/configs/config.py") for path in relative))
        self.assertTrue(any(path.endswith("/infer/rtrvc.py") for path in relative))
        self.assertTrue(any(path.endswith("/tools/cuda_graph.py") for path in relative))
        self.assertTrue(
            any(path.endswith("/RVCRealtimeVST/worker/rvc_worker.py") for path in relative)
        )
        self.assertTrue(
            any(path.endswith("/requirments_cu128_py312.txt") for path in relative)
        )


class _FakeWorker:
    def __init__(self, response):
        self.response = dict(response)
        self.requests = []

    def request(self, **payload):
        self.requests.append(payload)
        return dict(self.response)

    def close(self):
        return None


class RVCPrototypeFacadeTests(unittest.TestCase):
    def test_process_pcm_enforces_exact_block_size(self):
        prototype = rvc.RVCPrototype.__new__(rvc.RVCPrototype)
        prototype.worker = _FakeWorker({})
        prototype.sample_rate = 48000
        prototype.block_frames = 4
        prototype.block_ms = 0.0833
        prototype.last_metrics = {}

        with self.assertRaisesRegex(ValueError, "exactly 4"):
            prototype.process_pcm(b"\x00\x00" * 3)

    def test_process_pcm_round_trips_worker_audio_and_metrics(self):
        output = np.asarray([100, -100, 200, -200], dtype="<i2").tobytes()
        worker = _FakeWorker(
            {
                "ok": True,
                "event": "audio",
                "data": base64.b64encode(output).decode("ascii"),
                "samples": 4,
                "sample_rate": 48000,
                "inference_ms": 20.0,
                "deadline_ms": 40.0,
                "deadline_ratio": 0.5,
            }
        )
        prototype = rvc.RVCPrototype.__new__(rvc.RVCPrototype)
        prototype.worker = worker
        prototype.sample_rate = 48000
        prototype.block_frames = 4
        prototype.block_ms = 40.0
        prototype.last_metrics = {}

        result = prototype.process_pcm(
            b"\x00\x00" * 4,
            pitch=2.0,
            formant=-0.25,
            f0_method="rmvpe",
        )

        self.assertEqual(result, output)
        self.assertEqual(prototype.last_metrics["deadline_ratio"], 0.5)
        request = worker.requests[-1]
        self.assertEqual(request["cmd"], "process")
        self.assertEqual(request["pitch"], 2.0)
        self.assertEqual(request["formant"], -0.25)
        self.assertEqual(request["f0_method"], "rmvpe")


def load_worker_module():
    worker_path = Path(__file__).resolve().parents[1] / "engines" / "rvc" / "rvc_worker.py"
    spec = importlib.util.spec_from_file_location("test_rvc_worker_module", worker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RVCWorkerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_module = load_worker_module()

    def test_pcm16_decode_requires_exact_frame_count(self):
        raw = np.asarray([0, 100, -100, 32767], dtype="<i2").tobytes()
        encoded = base64.b64encode(raw).decode("ascii")

        values = self.worker_module.pcm16_to_float(encoded, 4)

        self.assertEqual(values.dtype, np.float32)
        self.assertEqual(len(values), 4)
        with self.assertRaisesRegex(ValueError, "block mismatch"):
            self.worker_module.pcm16_to_float(encoded, 3)

    def test_float_to_pcm16_clips_to_signed_16_range(self):
        raw = self.worker_module.float_to_pcm16(
            np.asarray([-2.0, -1.0, 0.0, 1.0, 2.0], dtype=np.float32)
        )
        values = np.frombuffer(raw, dtype="<i2").astype(np.int32)

        self.assertEqual(values[0], -32767)
        self.assertEqual(values[-1], 32767)
        self.assertEqual(values[2], 0)


if __name__ == "__main__":
    unittest.main()
