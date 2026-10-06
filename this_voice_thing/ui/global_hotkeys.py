"""Windows global hotkeys for Live Voice.

Uses RegisterHotKey rather than a low-level keyboard hook. Parsing is platform
independent and tested separately; registration is Windows-only.
"""

import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QTimer


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "ctrl": ("Ctrl", MOD_CONTROL),
    "control": ("Ctrl", MOD_CONTROL),
    "alt": ("Alt", MOD_ALT),
    "shift": ("Shift", MOD_SHIFT),
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift")

_SPECIAL_KEYS = {
    "space": ("Space", 0x20),
    "escape": ("Escape", 0x1B),
    "esc": ("Escape", 0x1B),
    "enter": ("Enter", 0x0D),
    "return": ("Enter", 0x0D),
    "tab": ("Tab", 0x09),
    "left": ("Left", 0x25),
    "up": ("Up", 0x26),
    "right": ("Right", 0x27),
    "down": ("Down", 0x28),
    "home": ("Home", 0x24),
    "end": ("End", 0x23),
    "pageup": ("PageUp", 0x21),
    "pgup": ("PageUp", 0x21),
    "pagedown": ("PageDown", 0x22),
    "pgdn": ("PageDown", 0x22),
    "insert": ("Insert", 0x2D),
    "delete": ("Delete", 0x2E),
}


class HotkeyError(ValueError):
    pass


def parse_hotkey(shortcut):
    """Return (canonical, modifiers, virtual_key).

    Live Voice deliberately requires at least one modifier so a global shortcut
    cannot consume ordinary typing while another application has focus.
    """
    raw = str(shortcut or "").strip()
    if not raw:
        raise HotkeyError("Enter a shortcut such as Ctrl+Alt+1.")

    parts = [part.strip() for part in raw.split("+") if part.strip()]
    if len(parts) < 2:
        raise HotkeyError("Global shortcuts require at least one modifier, for example Ctrl+Alt+1.")

    modifier_names = set()
    modifiers = 0
    key_name = None
    virtual_key = None

    for part in parts:
        lowered = part.lower().replace(" ", "")
        if lowered in _MODIFIERS:
            canonical, flag = _MODIFIERS[lowered]
            if canonical in modifier_names:
                raise HotkeyError(f"{canonical} is listed more than once.")
            modifier_names.add(canonical)
            modifiers |= flag
            continue

        if key_name is not None:
            raise HotkeyError("Use exactly one non-modifier key.")

        if len(part) == 1 and part.isalpha():
            key_name = part.upper()
            virtual_key = ord(key_name)
        elif len(part) == 1 and part.isdigit():
            key_name = part
            virtual_key = ord(part)
        elif lowered.startswith("f") and lowered[1:].isdigit():
            number = int(lowered[1:])
            if number == 12:
                raise HotkeyError("F12 is reserved by Windows for debuggers; choose another key.")
            if 1 <= number <= 24:
                key_name = f"F{number}"
                virtual_key = 0x70 + number - 1
            else:
                raise HotkeyError("Function keys must be F1 through F24.")
        elif lowered in _SPECIAL_KEYS:
            key_name, virtual_key = _SPECIAL_KEYS[lowered]
        else:
            raise HotkeyError(
                "Unsupported key. Use A-Z, 0-9, F1-F24 except F12, Space, Escape, Enter, Tab, arrows, "
                "Home/End, PageUp/PageDown, Insert or Delete."
            )

    if not modifier_names:
        raise HotkeyError("Global shortcuts require Ctrl, Alt or Shift.")
    if key_name is None:
        raise HotkeyError("Add one non-modifier key to the shortcut.")

    ordered = [name for name in _MODIFIER_ORDER if name in modifier_names]
    return "+".join(ordered + [key_name]), modifiers, virtual_key


def normalize_hotkey(shortcut):
    return parse_hotkey(shortcut)[0]


class GlobalHotkeyManager(QAbstractNativeEventFilter):
    """Register action IDs on the GUI thread and receive system-wide WM_HOTKEY."""

    FIRST_ID = 0x6200
    LAST_ID = 0x6FFF

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.supported = sys.platform.startswith("win")
        self._by_id = {}
        self._by_shortcut = {}
        self._next_id = self.FIRST_ID
        self._application = QCoreApplication.instance()
        self._installed = False
        if self.supported and self._application is not None:
            self._application.installNativeEventFilter(self)
            self._installed = True

    def clear(self):
        if self.supported and self._by_id:
            user32 = ctypes.windll.user32
            for hotkey_id in list(self._by_id):
                try:
                    user32.UnregisterHotKey(None, hotkey_id)
                except Exception:
                    pass
        self._by_id.clear()
        self._by_shortcut.clear()
        self._next_id = self.FIRST_ID

    def close(self):
        self.clear()
        if self._installed and self._application is not None:
            try:
                self._application.removeNativeEventFilter(self)
            except Exception:
                pass
        self._installed = False

    def register(self, action_id, shortcut):
        if not str(shortcut or "").strip():
            return ""
        try:
            canonical, modifiers, virtual_key = parse_hotkey(shortcut)
        except HotkeyError as exc:
            return str(exc)

        if not self.supported:
            return "Global hotkeys are currently implemented on Windows only."
        if not self._installed:
            return "Qt's native event filter is not available."
        if canonical in self._by_shortcut:
            return f"{canonical} is already assigned inside This Voice Thing."
        if self._next_id > self.LAST_ID:
            return "Too many global hotkeys are registered."

        hotkey_id = self._next_id
        try:
            ok = bool(
                ctypes.windll.user32.RegisterHotKey(
                    None,
                    hotkey_id,
                    modifiers | MOD_NOREPEAT,
                    virtual_key,
                )
            )
        except Exception as exc:
            return f"Windows hotkey registration failed: {exc}"
        if not ok:
            return (
                f"Windows could not register {canonical}. Another application may already use it."
            )

        self._next_id += 1
        self._by_id[hotkey_id] = str(action_id)
        self._by_shortcut[canonical] = hotkey_id
        return ""

    def action_from_native_message(self, message):
        if not self.supported:
            return None
        try:
            address = int(message)
            native = wintypes.MSG.from_address(address)
        except Exception:
            return None
        if int(native.message) != WM_HOTKEY:
            return None
        return self._by_id.get(int(native.wParam))

    def nativeEventFilter(self, event_type, message):
        action_id = self.action_from_native_message(message)
        if not action_id:
            return False
        QTimer.singleShot(
            0,
            lambda action_id=action_id: self.window.handle_live_global_hotkey(action_id),
        )
        return True

    def registered_count(self):
        return len(self._by_id)

