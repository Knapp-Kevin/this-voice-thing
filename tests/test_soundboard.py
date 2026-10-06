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

    def test_named_boards_create_select_rename_and_persist(self):
        first = self.store.active_board()
        second = self.store.create_board("Gaming")

        self.assertEqual(self.store.active_board_id, second.id)
        self.assertEqual(second.name, "Gaming")

        renamed = self.store.rename_board(second.id, "Calls")
        self.assertEqual(renamed.name, "Calls")

        self.store.select_board(first.id)
        self.assertEqual(self.store.active_board_id, first.id)

        reloaded = SoundboardStore(self.temp.name)
        self.assertEqual(reloaded.active_board_id, first.id)
        self.assertEqual(reloaded.get_board(second.id).name, "Calls")

    def test_board_voice_and_route_defaults_persist(self):
        board = self.store.active_board()
        board.default_voice_id = "voice-123"
        board.route_profile = "discord"
        self.store.save()

        reloaded = SoundboardStore(self.temp.name)
        restored = reloaded.active_board()

        self.assertEqual(restored.default_voice_id, "voice-123")
        self.assertEqual(restored.route_profile, "discord")

    def test_duplicate_board_names_are_made_unique(self):
        first = self.store.create_board("Gaming")
        second = self.store.create_board("Gaming")

        self.assertEqual(first.name, "Gaming")
        self.assertEqual(second.name, "Gaming 2")

    def test_last_board_cannot_be_deleted(self):
        only = self.store.active_board()
        with self.assertRaisesRegex(ValueError, "last soundboard"):
            self.store.remove_board(only.id)

    def test_deleting_board_removes_owned_audio_and_selects_another(self):
        main = self.store.active_board()
        doomed = self.store.create_board("Temporary")
        source = os.path.join(self.temp.name, "source.wav")
        with wave.open(source, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x01\x00" * 16)
        pad = self.store.import_audio_pad(source, "Clip", board=doomed)
        owned = self.store.audio_path(pad)

        removed = self.store.remove_board(doomed.id)

        self.assertEqual(removed.id, doomed.id)
        self.assertFalse(os.path.exists(owned))
        self.assertEqual(self.store.active_board_id, main.id)

    def test_deleting_board_keeps_cache_referenced_by_other_board(self):
        main = self.store.active_board()
        shared = Pad(label="Shared", text="Same")
        key = self.store.cache_digest(shared, {"voice_fingerprint": "same"})
        shared.cache_key = key
        self.store.add_pad(shared, board=main)
        path = self.store.write_pcm_cache(key, b"\x00\x00" * 10, 16000)

        other = self.store.create_board("Other")
        duplicate = Pad(label="Shared", text="Same", cache_key=key)
        self.store.add_pad(duplicate, board=other)

        self.store.remove_board(other.id)

        self.assertTrue(os.path.exists(path))

    def test_pad_metadata_and_board_trigger_policy_persist(self):
        board = self.store.active_board()
        board.default_interrupt_policy = "ignore"
        pad = self.store.add_pad(Pad(
            label="Favorite",
            text="Hello.",
            favorite=True,
            tags=["meeting", "useful"],
            interrupt_policy="interrupt",
        ))
        self.store.save()

        reloaded = SoundboardStore(self.temp.name)
        restored_board = reloaded.active_board()
        restored_pad = reloaded.get_pad(pad.id)

        self.assertEqual(restored_board.default_interrupt_policy, "ignore")
        self.assertTrue(restored_pad.favorite)
        self.assertEqual(restored_pad.tags, ["meeting", "useful"])
        self.assertEqual(restored_pad.interrupt_policy, "interrupt")

    def test_move_pad_persists_order(self):
        first = self.store.add_pad(Pad(label="First", text="One"))
        second = self.store.add_pad(Pad(label="Second", text="Two"))

        self.assertTrue(self.store.move_pad(second.id, -1))

        reloaded = SoundboardStore(self.temp.name)
        self.assertEqual(
            [pad.id for pad in reloaded.active_board().pads],
            [second.id, first.id],
        )
        self.assertFalse(reloaded.move_pad(second.id, -1))

    def test_clear_cache_preserves_pads_audio_and_resets_provenance(self):
        pad = self.store.add_pad(Pad(label="Test", text="Hello."))
        key = self.store.cache_digest(pad, {"voice_fingerprint": "voice"})
        path = self.store.write_pcm_cache(key, b"\x00\x00" * 10, 16000)
        pad.cache_key = key
        pad.cache_provenance = "live-native-unwatermarked"
        self.store.save()

        removed = self.store.clear_cache()

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(path))
        restored = self.store.get_pad(pad.id)
        self.assertEqual(restored.cache_key, "")
        self.assertEqual(restored.cache_provenance, "")
        self.assertEqual(len(self.store.active_board().pads), 1)

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
