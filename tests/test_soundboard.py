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
        ))

        reloaded = SoundboardStore(self.temp.name)
        found = reloaded.get_pad(pad.id)

        self.assertIsNotNone(found)
        self.assertEqual(found.label, "BRB")
        self.assertEqual(found.text, "Be right back.")
        self.assertEqual(found.voice_id, "voice-1")

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

    def test_multiple_boards_persist_active_selection(self):
        second = self.store.add_board("Work")
        self.store.add_pad(Pad(label="Meeting", text="One moment."), board=second)

        reloaded = SoundboardStore(self.temp.name)

        self.assertEqual(reloaded.active_board().name, "Work")
        self.assertEqual(len(reloaded.active_board().pads), 1)
        self.assertEqual(reloaded.active_board().pads[0].label, "Meeting")

    def test_duplicate_board_names_are_made_unique(self):
        first = self.store.add_board("Games")
        second = self.store.add_board("Games")

        self.assertEqual(first.name, "Games")
        self.assertEqual(second.name, "Games 2")

    def test_rename_board_remains_unique(self):
        first = self.store.add_board("Work")
        second = self.store.add_board("Games")

        renamed = self.store.rename_board(second.id, "Work")

        self.assertEqual(renamed.name, "Work 2")
        self.assertEqual(first.name, "Work")

    def test_cannot_delete_last_board(self):
        only = self.store.active_board()

        removed = self.store.remove_board(only.id)

        self.assertIsNone(removed)
        self.assertEqual(len(self.store.boards), 1)

    def test_deleting_active_board_selects_remaining_board(self):
        original = self.store.active_board()
        second = self.store.add_board("Second")
        self.assertEqual(self.store.active_board_id, second.id)

        removed = self.store.remove_board(second.id)

        self.assertEqual(removed.id, second.id)
        self.assertEqual(self.store.active_board_id, original.id)

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
