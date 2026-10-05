"""Running a generation: preview and full renders, progress, Stop, and the finished take."""

import os
import time

from PySide6.QtCore import QTime
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMessageBox

from this_voice_thing.core import documents
from this_voice_thing.ui.common import preview_cut
from this_voice_thing.ui.threads import AudioGeneratorThread


class Generation:
    """Starting, tracking and finishing a generation. Mixed into ChatterboxApp."""

    def handle_generate_stop_toggle(self):
        if not self.is_generating:
            self.start_generation(preview=False)
            return
        if hasattr(self, 'audio_generator_thread') and self.audio_generator_thread.isRunning():
            print("UI: Requesting stop for audio_generator_thread")
            self.audio_generator_thread.stop()
            self.generate_button.setText("Stopping...")
            self.generate_button.setEnabled(False)
            self.set_status_message(
                "Status: Stopping after the current section. Finished sections will be kept.")
        else:
            print("UI: Stop requested, but no active generation thread found. Resetting UI.")
            self.on_generation_thread_finished()

    def preview_text(self):
        """(text, character budget): a selection is previewed whole; otherwise a short
        excerpt from the start of the text, about the length picked next to Preview."""
        selected = self.text_input.textCursor().selectedText().replace("\u2029", "\n").strip()
        if selected:
            return selected, None
        return documents.excerpt(self.text_input.toPlainText(), self.preview_length_combo.currentData()), None

    def start_generation(self, preview=False):
        if self.is_generating:
            return
        if self.api_busy:
            self.set_status_message("Status: Busy with a request from the local API; try again in a moment.")
            return
        if self.model is None:
            QMessageBox.warning(self, "Model Not Loaded", "Please load the model first.")
            return
        # Keep the settings this render uses even if the app is closed abruptly later.
        self.save_app_settings()
        preview_budget = None
        if preview:
            text, preview_budget = self.preview_text()
        else:
            text = self.text_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Input Error", "Please enter some text to synthesize.")
            return
        qwen_problem = self.prepare_qwen_generation()
        if qwen_problem:
            QMessageBox.information(self, "Voice", qwen_problem)
            return

        self.is_generating = True
        self.generation_is_preview = preview
        self.generation_char_count = len(text)
        plan_entry = self.loaded_entry() or self.get_selected_model_entry()
        lengths = self.section_lengths(text, plan_entry)
        if preview:
            lengths = lengths[:preview_cut(lengths, preview_budget)]
        self.generation_plan = self.batch_plan(plan_entry, lengths)
        self.generation_estimate = sum(cost for _f, _l, cost in self.generation_plan)
        self.progress_range = None
        self.progress_done_cost = 0.0
        self.progress_done_time = 0.0
        self.generation_started_at = time.monotonic()
        self.keep_take_button.setVisible(False)
        self.generate_button.setText("Stop")
        self.generate_button.setEnabled(True)
        self.preview_button.setEnabled(False)
        self.open_document_button.setEnabled(False)
        self.model_repo_combo.setEnabled(False)
        self.generation_progress.setValue(0)
        self.generation_progress.setVisible(True)
        self.activity_label.setText("Previewing..." if preview else "Starting...")

        self.generation_start_time = QTime.currentTime()
        self.generation_timer.start(1000)
        self.update_generation_time_display()

        self.audio_generator_thread = AudioGeneratorThread(
            self.model, text,
            self.reference_path,
            self.exaggeration_slider.get_value(),
            self.temp_slider.get_value(),
            self.cfg_slider.get_value(),
            self.seed_input.value(),
            self.output_directory,
            language_id=self.language_combo.currentData() or "en",
            repetition_penalty=self.repetition_penalty,
            min_p=self.min_p,
            top_p=self.top_p,
            finishing=self.current_finishing_settings(),
            output_name=self.current_document_name,
            preview=preview,
        )
        self.audio_generator_thread.pronunciations = self.pronunciations
        self.audio_generator_thread.preview_chars = preview_budget
        self.audio_generator_thread.generation_complete.connect(self.on_generation_complete)
        self.audio_generator_thread.error_occurred.connect(self.on_generation_error)
        self.audio_generator_thread.chunk_generated.connect(self.on_chunk_generated_progress)
        self.audio_generator_thread.section_timed.connect(self.on_section_timed)
        self.audio_generator_thread.finished.connect(self.on_generation_thread_finished)
        self.audio_generator_thread.start()

    def keep_preview_take(self):
        if self.last_preview_seed:
            self.seed_input.setValue(self.last_preview_seed)
            self.keep_take_button.setVisible(False)
            self.activity_label.setText(f"Take {self.last_preview_seed} locked")

    def on_generation_thread_finished(self):
        print("UI: audio_generator_thread.finished signal received.")

        # Stop the timer regardless of how the thread finished
        if self.generation_timer.isActive():
            print("UI: Stopping generation timer.")
            self.generation_timer.stop()

        # Reset UI elements
        self.is_generating = False
        self.generate_button.setText("Generate Audio")
        self.generate_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.open_document_button.setEnabled(True)
        self.model_repo_combo.setEnabled(not getattr(self, "model_is_loading", False))
        self.update_model_details()
        self.generation_progress.setVisible(False)
        if not self.keep_take_button.isVisible():
            self.activity_label.clear()
        self.update_text_stats()

        # Final status update based on how the thread might have ended,
        # if not already set by on_generation_complete or on_generation_error.
        # This ensures "Stopping..." doesn't linger.
        current_status = self.status_bar.text()
        if "stopping generation..." in current_status.lower() or \
           "stop requested." in current_status.lower():
            self.set_status_message("Status: Generation stopped by user.")
        elif not any(marker in current_status.lower() for marker in (
                "full audio generated", "failed", "stopped by user",
                "preview ready", "stopped. saved")):
            # If no specific completion or error message was set, default to Ready
            self.set_status_message("Status: Ready.")

    def update_generation_time_display(self):
        self.refresh_progress_activity()
        if self.generation_start_time and self.is_generating:
            elapsed_ms = self.generation_start_time.msecsTo(
                QTime.currentTime())
            # Only update if not showing chunk progress, to avoid flicker
            # and if the button still says "Stop Generation" (i.e. not "Stopping...")
            if "chunk" not in self.status_bar.text().lower() and \
               self.generate_button.text() == "Stop Generation":
                self.set_status_message(
                    f"Status: Generating... (Elapsed: {self.format_time(elapsed_ms)})")
        elif not self.is_generating and self.generation_timer.isActive():
            # This is a failsafe, should be stopped by on_generation_thread_finished
            print(
                "UI: Generation timer stopped by failsafe in update_generation_time_display.")
            self.generation_timer.stop()

    def update_generation_time(self):
        if self.generation_start_time and self.is_generating:
            elapsed_ms = self.generation_start_time.msecsTo(
                QTime.currentTime())
            seconds = int((elapsed_ms / 1000) % 60)
            minutes = int((elapsed_ms / (1000 * 60)) % 60)
            self.set_status_message(
                f"Status: Generating audio... {minutes:02}:{seconds:02}")

    def on_chunk_generated_progress(self, first, last, total):
        if not self.is_generating:
            return
        done = first - 1
        elapsed = time.monotonic() - self.generation_started_at
        # Everything before this batch has just finished: calibrate against the plan.
        self.progress_done_cost = sum(cost for _f, end, cost in getattr(self, "generation_plan", [])
                                      if end <= done)
        self.progress_done_time = elapsed
        self.progress_range = (first, last, total)
        self.generation_progress.setMaximum(total)
        self.generation_progress.setValue(done)
        span = f"{first}" if first == last else f"{first}\u2013{last}"
        self.set_status_message(f"Status: Generating section {span} of {total}...")
        self.refresh_progress_activity()

    def refresh_progress_activity(self):
        """Activity text with a countdown; called on progress and every second."""
        if not self.is_generating or not getattr(self, "progress_range", None):
            return
        first, last, total = self.progress_range
        elapsed = time.monotonic() - self.generation_started_at
        estimate = getattr(self, "generation_estimate", 0.0)
        if self.progress_done_cost > 0:
            projected = self.progress_done_time / self.progress_done_cost * estimate
        else:
            projected = estimate
        remaining = max(projected - elapsed, 0.0)
        span = f"{first}" if first == last else f"{first}\u2013{last}"
        activity = f"{span}/{total}"
        if self.generation_is_preview:
            activity = f"Preview {activity}"
        if estimate > 0:
            activity += f" \u00b7 {self.format_clock(remaining)} left" if remaining >= 1 else " \u00b7 finishing"
        self.activity_label.setText(activity)

    def on_generation_complete(self, output_path, sample_rate):
        # self.is_generating will be set to False by on_generation_thread_finished
        # self.generation_timer will be stopped by on_generation_thread_finished

        total_generation_time_str = ""
        if self.generation_start_time:
            elapsed_ms = self.generation_start_time.msecsTo(
                QTime.currentTime())
            total_generation_time_str = f" (Total time: {self.format_time(elapsed_ms)})"

        thread = self.audio_generator_thread
        self.generation_progress.setValue(self.generation_progress.maximum())
        if thread.preview:
            self.last_preview_seed = thread.actual_seed_used
            self.activity_label.setText(f"Preview take {self.last_preview_seed}")
            self.keep_take_button.setVisible(self.seed_input.value() == 0)
            self.set_status_message(f"Status: Preview ready{total_generation_time_str}.")
        elif thread.partial_info:
            done, total = thread.partial_info
            missing = f"section {done + 1}" if done + 1 == total else f"sections {done + 1}-{total}"
            self.set_status_message(
                f"Status: Stopped, so {missing} of {total} weren't generated. Saved the first {done}: "
                f"{os.path.basename(output_path)}")
        else:
            captions = f" + {os.path.basename(thread.subtitle_path)}" if thread.subtitle_path else ""
            self.set_status_message(
                f"Status: Full audio generated: {os.path.basename(output_path)}{captions}{total_generation_time_str}")

        self.current_audio_file = output_path
        # ... (rest of the method same as your working version)
        self.current_file_label.setText(
            f"Last generated: {os.path.basename(output_path)}")
        self.media_player.setSource(QUrl.fromLocalFile(output_path))
        self.play_pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.playhead_slider.setEnabled(True)
        self.update_output_log()
        if self.autoplay_checkbox.isChecked() or thread.preview:
            self.media_player.play()

    def on_generation_error(self, error_msg):
        # self.is_generating will be set to False by on_generation_thread_finished
        # self.generation_timer will be stopped by on_generation_thread_finished

        is_user_stop = "stopped by user" in error_msg.lower()

        final_status_msg = f"Status: {'Generation stopped by user.' if is_user_stop else 'Generation failed.'}"
        if not is_user_stop and error_msg:
            first_line_error = error_msg.splitlines()[0]
            if len(first_line_error) > 70:
                # Adjusted length
                first_line_error = first_line_error[:67] + "..."
            final_status_msg += f" ({first_line_error})"

        self.set_status_message(final_status_msg)

        if not is_user_stop:
            QMessageBox.critical(self, "Generation Error", error_msg)
        else:
            print(f"User stop confirmed by error signal: {error_msg}")
