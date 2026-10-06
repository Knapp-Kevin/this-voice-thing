import unittest

from this_voice_thing.ui.global_hotkeys import HotkeyError, normalize_hotkey, parse_hotkey


class GlobalHotkeyParsingTests(unittest.TestCase):
    def test_normalizes_modifier_order_and_aliases(self):
        self.assertEqual(normalize_hotkey("alt+control+k"), "Ctrl+Alt+K")
        self.assertEqual(normalize_hotkey("shift+alt+f11"), "Alt+Shift+F11")
        self.assertEqual(normalize_hotkey("ctrl+esc"), "Ctrl+Escape")

    def test_requires_at_least_one_modifier(self):
        with self.assertRaisesRegex(HotkeyError, "require"):
            parse_hotkey("F8")

    def test_rejects_multiple_non_modifier_keys(self):
        with self.assertRaisesRegex(HotkeyError, "exactly one"):
            parse_hotkey("Ctrl+A+B")

    def test_rejects_duplicate_modifiers(self):
        with self.assertRaisesRegex(HotkeyError, "more than once"):
            parse_hotkey("Ctrl+Control+K")

    def test_accepts_documented_key_ranges(self):
        for value in (
            "Ctrl+Alt+1",
            "Ctrl+Shift+Z",
            "Alt+F24",
            "Ctrl+Space",
            "Ctrl+Alt+PageDown",
        ):
            canonical, modifiers, virtual_key = parse_hotkey(value)
            self.assertTrue(canonical)
            self.assertGreater(modifiers, 0)
            self.assertGreater(virtual_key, 0)

    def test_rejects_unknown_key(self):
        with self.assertRaisesRegex(HotkeyError, "Unsupported key"):
            parse_hotkey("Ctrl+VolumeUp")

    def test_rejects_windows_modifier(self):
        with self.assertRaises(HotkeyError):
            parse_hotkey("Win+K")

    def test_rejects_reserved_f12(self):
        with self.assertRaisesRegex(HotkeyError, "reserved"):
            parse_hotkey("Ctrl+F12")


if __name__ == "__main__":
    unittest.main()
