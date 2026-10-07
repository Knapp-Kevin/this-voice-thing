import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from this_voice_thing.core.live_voice import AudioFrame
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

    def test_reset_stream_state_sends_worker_reset(self):
        prototype = rvc.RVCPrototype.__new__(rvc.RVCPrototype)
        prototype.worker = _FakeWorker({"ok": True, "event": "reset"})
        prototype.sample_rate = 48000
        prototype.block_frames = 4
        prototype.block_ms = 0.0833
        prototype.last_metrics = {}

        reply = prototype.reset_stream_state()

        self.assertEqual(reply["event"], "reset")
        self.assertEqual(prototype.worker.requests[-1]["cmd"], "reset")

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


class _FakePrototype:
    def __init__(self, block_frames=4, sample_rate=48000):
        self.block_frames = int(block_frames)
        self.sample_rate = int(sample_rate)
        self.last_metrics = {}
        self.blocks = []
        self.reset_calls = 0

    def process_pcm(self, pcm, **_kwargs):
        self.blocks.append(bytes(pcm))
        self.last_metrics = {
            "deadline_ratio": 0.5 if len(self.blocks) == 1 else 1.25,
        }
        return bytes(pcm)

    def reset_stream_state(self):
        self.reset_calls += 1
        return {"ok": True, "event": "reset"}


class RVCFrameAdapterTests(unittest.TestCase):
    def test_accumulates_arbitrary_audio_frames_into_exact_blocks(self):
        prototype = _FakePrototype(block_frames=4)
        adapter = rvc.RVCFrameAdapter(prototype)

        first = adapter.process_frame(
            AudioFrame(pcm=b"\x01\x00" * 2, sample_rate=48000)
        )
        second = adapter.process_frame(
            AudioFrame(pcm=b"\x02\x00" * 6, sample_rate=48000)
        )

        self.assertEqual(first, [])
        self.assertEqual(len(second), 2)
        self.assertEqual(prototype.blocks[0], b"\x01\x00" * 2 + b"\x02\x00" * 2)
        self.assertEqual(prototype.blocks[1], b"\x02\x00" * 4)
        self.assertTrue(all(frame.provenance == "rvc-neural-conversion" for frame in second))
        self.assertEqual(adapter.metrics()["deadline_misses"], 1)

    def test_discontinuity_discards_pending_input_and_resets_worker_state(self):
        prototype = _FakePrototype(block_frames=4)
        adapter = rvc.RVCFrameAdapter(prototype)

        adapter.process_frame(
            AudioFrame(pcm=b"\x01\x00" * 3, sample_rate=48000)
        )
        output = adapter.process_frame(
            AudioFrame(
                pcm=b"\x02\x00" * 4,
                sample_rate=48000,
                discontinuity=True,
            )
        )

        self.assertEqual(prototype.reset_calls, 1)
        self.assertEqual(prototype.blocks, [b"\x02\x00" * 4])
        self.assertEqual(len(output), 1)
        self.assertTrue(output[0].discontinuity)
        self.assertEqual(adapter.metrics()["discontinuities"], 1)

    def test_rejects_incompatible_frame_contract(self):
        prototype = _FakePrototype(block_frames=4)
        adapter = rvc.RVCFrameAdapter(prototype)

        with self.assertRaisesRegex(ValueError, "mono"):
            adapter.process_frame(
                AudioFrame(pcm=b"\x00\x00" * 8, sample_rate=48000, channels=2)
            )
        with self.assertRaisesRegex(ValueError, "48000"):
            adapter.process_frame(
                AudioFrame(pcm=b"\x00\x00" * 4, sample_rate=44100)
            )
        with self.assertRaisesRegex(ValueError, "signed-16"):
            adapter.process_frame(
                AudioFrame(
                    pcm=b"\x00\x00" * 4,
                    sample_rate=48000,
                    sample_format="f32le",
                )
            )


class RVCWorkerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker_module = load_worker_module()

    def test_validate_upstream_requires_pinned_source_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in self.worker_module.required_upstream_files(root):
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.name == ".this-voice-thing-source.json":
                    path.write_text(
                        '{"commit": "wrong"}',
                        encoding="utf-8",
                    )
                else:
                    path.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "source revision mismatch"):
                self.worker_module.validate_upstream(root)

            (root / ".this-voice-thing-source.json").write_text(
                json.dumps({"commit": self.worker_module.UPSTREAM_COMMIT}),
                encoding="utf-8",
            )
            self.assertEqual(self.worker_module.validate_upstream(root), root.resolve())

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
