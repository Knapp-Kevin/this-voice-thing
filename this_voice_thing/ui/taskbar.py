"""The app's identity in the Windows taskbar.

Run from Python, the window would otherwise be grouped under python.exe (Python's
icon), or under a taskbar ID whose icon Windows has cached as the generic one. So
the process gets its own ID, and the window carries that ID plus an explicit icon
file and relaunch command; Windows then shows the brand icon, and pinning the app
pins This Voice Thing rather than Python. Everything here is a no-op off Windows.
"""

import ctypes
import os
import sys
from ctypes import wintypes

from this_voice_thing import paths

APP_ID = "KnappKevin.ThisVoiceThing"
APP_NAME = "This Voice Thing"
ICON_FILE = os.path.join(paths.ASSETS_DIR, "branding", "this-voice-thing.ico")

VT_LPWSTR = 31
# PKEY_AppUserModel_*: {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}
RELAUNCH_COMMAND, RELAUNCH_ICON, RELAUNCH_NAME, ID = 2, 3, 4, 5


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(self))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("reserved1", ctypes.c_ushort), ("reserved2", ctypes.c_ushort),
                ("reserved3", ctypes.c_ushort), ("value", ctypes.c_void_p), ("padding", ctypes.c_void_p)]


def set_process_app_id():
    """Call before QApplication, so every window starts in the app's own taskbar group."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def relaunch_command():
    """How Windows should start the app from a pinned taskbar button: the project's own
    environment (the GUI interpreter, so no console) running main.py."""
    venv = os.path.join(paths.ROOT, ".venv", "Scripts", "pythonw.exe")
    python = venv if os.path.exists(venv) else sys.executable
    return f'"{python}" "{os.path.join(paths.ROOT, "main.py")}"'


def set_window_identity(window):
    """Attach the app ID, icon file and relaunch command to a top-level window
    (winId() creates its native window, so this works before show())."""
    if sys.platform != "win32" or not os.path.exists(ICON_FILE):
        return False
    try:
        store = ctypes.c_void_p()
        iid = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")  # IPropertyStore
        ctypes.oledll.shell32.SHGetPropertyStoreForWindow(
            wintypes.HWND(int(window.winId())), ctypes.byref(iid), ctypes.byref(store))
        vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0],
                             ctypes.POINTER(ctypes.c_void_p))
        set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                       ctypes.POINTER(PROPVARIANT))(vtable[6])
        commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtable[7])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        fmtid = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
        keep = []  # the strings must outlive the calls
        try:
            # The relaunch values first; the ID last (Windows reads them when the ID is set).
            for pid, text in ((RELAUNCH_COMMAND, relaunch_command()), (RELAUNCH_ICON, ICON_FILE),
                              (RELAUNCH_NAME, APP_NAME), (ID, APP_ID)):
                buffer = ctypes.create_unicode_buffer(text)
                keep.append(buffer)
                value = PROPVARIANT(vt=VT_LPWSTR, value=ctypes.cast(buffer, ctypes.c_void_p).value)
                set_value(store, ctypes.byref(PROPERTYKEY(GUID(fmtid), pid)), ctypes.byref(value))
            commit(store)
        finally:
            release(store)
        return True
    except Exception as exc:
        print(f"Taskbar identity not set: {exc}")
        return False
