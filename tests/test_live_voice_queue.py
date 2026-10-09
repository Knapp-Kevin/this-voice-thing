"""Live Voice queue flow after Stop all (regression: a misspelled attribute kept the
Stop-all flag set, so later queues stalled after their first item and playback
never finished)."""

import unittest

from this_voice_thing.ui.pages.live_voice import LiveVoicePage


class _Label:
    def __init__(self):
        self.value = ""

    def setText(self, text):
        self.value = text

    def text(self):
        return self.value


class _Output:
    def __init__(self):
        self.finished = 0
        self.stopped = 0

    def finish_input(self):
        self.finished += 1

    def stop(self):
        self.stopped += 1

    def route_description(self):
        return ""

    def is_playing(self):
        return False


class _Host(LiveVoicePage):
    """Just enough of the window for the queue methods."""

    def __init__(self):
        self.live_voice_queue = []
        self.live_voice_current = None
        self.live_speech_thread = None
        self.live_voice_last_error = ""
        self.live_voice_stop_all_requested = False
        self.live_status_label = _Label()
        self.live_current_label = _Label()
        self.live_audio_output = _Output()
        self.live_monitor_output = _Output()
        self.started = []

    def _refresh_live_queue(self):
        pass

    def _set_live_generation_busy(self, busy):
        self.busy = busy

    def _estimate_live_item_seconds(self, text):
        return 2.0

    def _live_start_next(self):
        self.started.append(self.live_voice_queue.pop(0))


class LiveVoiceQueueAfterStopAllTests(unittest.TestCase):
    def test_new_speech_clears_the_stop_all_flag(self):
        host = _Host()
        host.live_stop_all()
        self.assertTrue(host.live_voice_stop_all_requested)
        self.assertTrue(host._enqueue_live_item({"text": "Hello again."}))
        self.assertFalse(host.live_voice_stop_all_requested)

    def test_queue_advances_after_a_previous_stop_all(self):
        host = _Host()
        host.live_stop_all()
        host._enqueue_live_item({"text": "First."})
        host._enqueue_live_item({"text": "Second."})
        host.live_voice_current = host.live_voice_queue.pop(0)  # "First." is speaking
        host.on_live_thread_finished()
        self.assertEqual([item["text"] for item in host.started], ["Second."])

    def test_last_item_finishes_playback_after_a_previous_stop_all(self):
        host = _Host()
        host.live_stop_all()
        host._enqueue_live_item({"text": "Only."})
        host.live_voice_current = host.live_voice_queue.pop(0)
        host.on_live_thread_finished()
        self.assertEqual(host.live_audio_output.finished, 1)


if __name__ == "__main__":
    unittest.main()
