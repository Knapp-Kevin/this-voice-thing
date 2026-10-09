import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from this_voice_thing.engines import rvc_feasibility as rvc


class RvcFeasibilityTests(unittest.TestCase):
    def test_sanitize_requirements_removes_upstream_mirror_directives(self):
        text = """--index-url https://mirror.example/simple
--extra-index-url https://torch.example
numpy>=1.26,<2
# comment
soundfile>=0.13,<1
"""
        result = rvc.sanitize_requirements(text)

        self.assertNotIn("--index-url", result)
        self.assertNotIn("--extra-index-url", result)
        self.assertIn("numpy>=1.26,<2", result)
        self.assertIn("soundfile>=0.13,<1", result)

    def test_requirements_reject_unapproved_package_sources(self):
        unsafe_lines = [
            "--find-links https://packages.example/wheels",
            "--trusted-host packages.example",
            "--no-index",
            "-r nested-requirements.txt",
            "--requirement nested-requirements.txt",
            "-c constraints.txt",
            "-e git+https://example.com/project.git",
            "git+https://example.com/project.git",
            "sample @ https://example.com/sample.whl",
            "https://example.com/sample.whl",
        ]
        for line in unsafe_lines:
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, "line 2"):
                    rvc.sanitize_requirements("numpy==1.26.0\\n" + line + "\\n")

    def test_requirements_preserve_constraints_comments_and_strip_indexes(self):
        content = (
            "--index-url=https://mirror.example/simple\\n"
            "-i https://another.example/simple\\n"
            "--extra-index-url https://extra.example/simple\\n"
            "numpy>=1.26,<2  # numeric support\\n"
            "soundfile==0.13.1\\n"
        )
        sanitized = rvc.sanitize_requirements(content)
        self.assertEqual(sanitized, "numpy>=1.26,<2  # numeric support\\nsoundfile==0.13.1\\n")

    def test_install_commands_pin_python_torch_and_upstream_revision(self):
        commands = [command for command, _cwd in rvc.install_commands("uv", "git")]
        flattened = [" ".join(command) for command in commands]

        self.assertTrue(any(rvc.UPSTREAM_COMMIT in command for command in flattened))
        self.assertTrue(any("--python 3.12" in command for command in flattened))
        self.assertTrue(any("torch==2.7.1+cu128" in command for command in flattened))
        self.assertTrue(any(rvc.TORCH_INDEX in command for command in flattened))
        self.assertTrue(any(rvc.PYPI_INDEX in command for command in flattened))

    def test_shared_assets_are_pinned_by_revision_and_checksum(self):
        self.assertEqual(len(rvc.ASSET_REVISION), 40)
        int(rvc.ASSET_REVISION, 16)
        self.assertEqual(len(rvc.HUBERT_MODEL_SHA256), 64)
        self.assertEqual(len(rvc.RMVPE_SHA256), 64)
        self.assertNotEqual(rvc.ASSET_REVISION, "main")

    def test_offline_command_uses_pinned_environment_and_does_not_fake_formant(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "voice.pth"
            source = Path(tmp) / "input.wav"
            output = Path(tmp) / "output.wav"
            model.touch()
            source.touch()

            command = rvc.build_offline_command(
                model=str(model),
                input_path=str(source),
                output_path=str(output),
                pitch=3,
                f0_method="rmvpe",
                index_rate=0,
            )

            self.assertEqual(command[0], str(rvc.venv_python()))
            self.assertIn(str(rvc.SOURCE_DIR / "infer" / "cli.py"), command)
            self.assertIn("3", command)
            self.assertIn("--overwrite", command)

            with self.assertRaises(ValueError):
                rvc.build_offline_command(
                    model=str(model),
                    input_path=str(source),
                    output_path=str(output),
                    formant=2,
                )

    @mock.patch("this_voice_thing.engines.rvc_feasibility._source_revision")
    @mock.patch("this_voice_thing.engines.rvc_feasibility.subprocess.check_output")
    @mock.patch("this_voice_thing.engines.rvc_feasibility.Path.exists")
    def test_status_reports_asset_pin(self, exists, check_output, source_revision):
        exists.return_value = False
        source_revision.return_value = None
        check_output.return_value = "Python 3.12.9\n"

        result = rvc.status()

        self.assertEqual(result["asset_repository"], rvc.ASSET_REPOSITORY)
        self.assertEqual(result["asset_revision"], rvc.ASSET_REVISION)
        self.assertFalse(result["shared_assets_ready"])

    @mock.patch("this_voice_thing.engines.rvc_feasibility._source_revision")
    @mock.patch("this_voice_thing.engines.rvc_feasibility.subprocess.check_output")
    @mock.patch("this_voice_thing.engines.rvc_feasibility.Path.exists")
    def test_status_requires_exact_revision(self, exists, check_output, source_revision):
        exists.return_value = True
        source_revision.return_value = "different-revision"
        check_output.return_value = "Python 3.12.9\n"

        result = rvc.status()

        self.assertFalse(result["source_matches_pin"])
        self.assertFalse(result["installed"])
        self.assertEqual(result["python_version"], "Python 3.12.9")


if __name__ == "__main__":
    unittest.main()
