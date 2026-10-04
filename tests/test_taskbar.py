"""The Windows taskbar identity: the icon file and relaunch command it points at."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from this_voice_thing import paths  # noqa: E402
from this_voice_thing.ui import taskbar  # noqa: E402


class TaskbarTests(unittest.TestCase):
    def test_icon_file_is_a_real_ico(self):
        with open(taskbar.ICON_FILE, "rb") as handle:
            self.assertEqual(handle.read(4), b"\x00\x00\x01\x00")  # ICO header

    def test_relaunch_runs_main_py_from_the_project(self):
        command = taskbar.relaunch_command()
        self.assertIn(os.path.join(paths.ROOT, "main.py"), command)
        self.assertTrue(command.startswith('"'))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_identity_attaches_before_show(self):
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        window = QWidget()
        self.assertTrue(taskbar.set_window_identity(window))
        self.assertFalse(window.isVisible())
        window.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
