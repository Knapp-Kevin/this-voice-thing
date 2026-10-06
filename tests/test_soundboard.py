import os
import tempfile
import unittest
import wave

from this_voice_thing.core.soundboard import Pad, SoundboardStore


class SoundboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SoundboardStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pad_persists_across_reload(self):
        pad = self.store.add_pad(Pad(
            label="BRB",
            text="Be right back.",
            voice_id="voice-1",
            model_repo_id="model/repo",
            model_backend="voxcpm",
            model_mode="clone",
            hotkey="Ctrl+Alt+1",
        ))

        reloaded = SoundboardStore(self.temp.name)
        found = reloaded.get_pad(pad.id)

        self.assertIsNotNone(found)
        self.assertEqual(found.label, "BRB")
        self.assertEqual(found.text, "Be right back.")
        self.assertEqual(found.voice_id, "voice-1")
        self.assertEqual(found.hotkey, "Ctrl+Alt+1")

    def test_duplicate_labels_are_made_unique(self):
        first = self.store.add_pad(Pad(label="Hello", text="One"))
        second = self.store.add_pad(Pad(label="Hello", text="Two"))

        self.assertEqual(first.label, "Hello")
        self.assertEqual(second.label, "Hello 2")

    def test_cache_digest_changes_with_synthesis_context(self):
        pad = Pad(label="Test", text="Hello.", temperature=0.7)
        a = self.store.cache_digest(pad, {"voice_fingerprint": "a"})
        b = self.store.cache_digest(pad, {"voice_fingerprint": "b"})

        self.assertNotEqual(a, b)

    def test_pcm_cache_is_valid_wav(self):
        pad = Pad(label="Test", text="Hello.")
        key = self.store.cache_digest(pad, {"voice_fingerprint": "voice"})
        pcm = (b"\x01\x00" * 480)

        path = self.store.write_pcm_cache(key, pcm, 48000)

        self.assertTrue(os.path.isfile(path))
        with wave.open(path, "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getframerate(), 48000)
            self.assertEqual(handle.readframes(480), pcm)

    def test_audio_pad_import_is_owned_persisted_and_deleted(self):
        source = os.path.join(self.temp.name, "source.wav")
        with wave.open(source, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x01\x00" * 32)

        pad = self.store.import_audio_pad(source, "Air horn")
        owned = self.store.audio_path(pad)

        self.assertTrue(os.path.isfile(owned))
        self.assertNotEqual(os.path.abspath(source), os.path.abspath(owned))

        reloaded = SoundboardStore(self.temp.name)
        found = reloaded.get_pad(pad.id)
        self.assertEqual(found.kind, "audio")
        self.assertTrue(os.path.isfile(reloaded.audio_path(found)))

        reloaded.remove_pad(found.id)
        self.assertFalse(os.path.exists(owned))

    def test_audio_path_rejects_directory_escape(self):
        pad = Pad(label="Bad", text="", kind="audio", audio_file="../outside.wav")
        self.assertEqual(self.store.audio_path(pad), "")

    def test_removing_last_reference_garbage_collects_cache(self):
        pad = self.store.add_pad(Pad(label="Test", text="Hello."))
        key = self.store.cache_digest(pad, {"voice_fingerprint": "voice"})
        path = self.store.write_pcm_cache(key, b"\x00\x00" * 10, 16000)
        pad.cache_key = key
        self.store.save()

        self.store.remove_pad(pad.id)

        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
