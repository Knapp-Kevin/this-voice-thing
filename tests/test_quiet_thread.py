"""The generator hides its own console output, but not what other threads print meanwhile."""

import io
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from this_voice_thing.ui.threads import quiet_this_thread  # noqa: E402


class QuietThreadTests(unittest.TestCase):
    def test_only_the_quieted_thread_is_silenced(self):
        captured = io.StringIO()
        saved = sys.stdout
        sys.stdout = captured
        try:
            with quiet_this_thread():
                print("model chatter")
                other = threading.Thread(target=lambda: print("Stop requested"))
                other.start()
                other.join()
            print("after")
        finally:
            sys.stdout = saved
        self.assertEqual(captured.getvalue(), "Stop requested\nafter\n")

    def test_streams_are_restored_after_an_error(self):
        saved = sys.stdout, sys.stderr
        with self.assertRaises(RuntimeError):
            with quiet_this_thread():
                raise RuntimeError("boom")
        self.assertEqual((sys.stdout, sys.stderr), saved)


if __name__ == "__main__":
    unittest.main()
