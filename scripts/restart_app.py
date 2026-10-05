"""Restart This Voice Thing gracefully (Windows): ask its window to close, so it saves
its settings, then start it again with run.bat. Forces it only if it hangs.

    python scripts/restart_app.py          restart
    python scripts/restart_app.py --close  just close it
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TITLE = "This Voice Thing"
WM_CLOSE = 0x0010


def app_windows():
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if buffer.value == TITLE and name.value.startswith("Qt"):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, pid.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def process_alive(pid):
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def close(timeout=30):
    windows = app_windows()
    if not windows:
        print("Not running.")
        return
    for hwnd, _pid in windows:
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    deadline = time.time() + timeout
    pids = {pid for _hwnd, pid in windows}
    while time.time() < deadline and any(process_alive(pid) for pid in pids):
        time.sleep(0.5)
    stuck = [pid for pid in pids if process_alive(pid)]
    if stuck:
        print(f"Still running after {timeout} s; forcing {stuck}.")
        for pid in stuck:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        print("Closed (settings saved).")


def start():
    subprocess.Popen(["cmd", "/c", "start", "", "/min", os.path.join(ROOT, "run.bat")], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("Started.")


if __name__ == "__main__":
    close()
    if "--close" not in sys.argv:
        time.sleep(1)
        start()
