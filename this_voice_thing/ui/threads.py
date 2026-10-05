"""Background threads: model loading, generation, engine installs, speech and transcription."""

import contextlib
import datetime
import os
import random
import sys
import threading
import time
import traceback

import numpy as np
import torch
from PySide6.QtCore import QThread, Signal

from this_voice_thing.core import audio_effects, documents, subtitles
from this_voice_thing.engines import (
    kokoro as kokoro_engine,
    omnivoice as omnivoice_engine,
    qwen as qwen_engine,
    vibevoice as vibevoice_engine,
    voxcpm as voxcpm_engine,
)
from this_voice_thing.ui.common import (
    BACKEND_MULTILINGUAL,
    CHATTERBOX_AVAILABLE,
    DEFAULT_MODEL_REPO,
    DEFAULT_MULTILINGUAL_T3_MODEL,
    DEFAULT_PREVIEW_CHARS,
    KOKORO_BACKEND,
    load_chatterbox_model,
    MAX_TEXT_INPUT_LENGTH,
    nltk,
    NLTK_RESOURCES_OK,
    OMNIVOICE_BACKEND,
    preview_cut,
    probe_usable_torch_cuda,
    QWEN_BACKEND,
    safe_console_text,
    UnsupportedChatterboxRepoError,
    VIBEVOICE_BACKEND,
    VOXCPM_BACKEND,
)


# --- ModelLoaderThread (Same as your working version) ---


class ModelLoaderThread(QThread):
    model_loaded = Signal(object, str)
    error_occurred = Signal(str)

    def __init__(
        self,
        repo_id=DEFAULT_MODEL_REPO,
        backend=BACKEND_MULTILINGUAL,
        multilingual_t3_model=DEFAULT_MULTILINGUAL_T3_MODEL,
    ):
        super().__init__()
        self.cuda_probe_error = None
        if torch.cuda.is_available():
            cuda_ok, cuda_error = probe_usable_torch_cuda()
            if cuda_ok:
                self.device = "cuda"
            else:
                self.device = "cpu"
                self.cuda_probe_error = cuda_error
        else:
            self.device = "cpu"
        self.repo_id = repo_id
        self.backend = backend
        self.multilingual_t3_model = multilingual_t3_model
        self.mode = "clone"

    def run(self):
        try:
            if not CHATTERBOX_AVAILABLE:
                self.error_occurred.emit(
                    "ChatterboxTTS library is not installed.")
                return
            if self.cuda_probe_error:
                print(
                    "CUDA was detected but is not usable with the current "
                    f"PyTorch build. Falling back to CPU. Reason: {self.cuda_probe_error}"
                )
            print(
                f"Attempting to load Chatterbox model from repo '{self.repo_id}' "
                f"using backend '{self.backend}' on device: {self.device}..."
            )
            if self.backend == QWEN_BACKEND:
                if not qwen_engine.is_installed():
                    raise RuntimeError(
                        "The Qwen engine isn't installed. Use Load this model on the Model page "
                        "to install it.")
                model_instance = qwen_engine.load_qwen_model(self.repo_id, log=print)
            elif self.backend == KOKORO_BACKEND:
                model_instance = kokoro_engine.load_kokoro_model(self.repo_id, log=print)
            elif self.backend == VOXCPM_BACKEND:
                model_instance = voxcpm_engine.load_voxcpm_model(self.repo_id, self.mode, log=print)
            elif self.backend == OMNIVOICE_BACKEND:
                model_instance = omnivoice_engine.load_omnivoice_model(self.repo_id, self.mode, log=print)
            elif self.backend == VIBEVOICE_BACKEND:
                model_instance = vibevoice_engine.load_vibevoice_model(self.repo_id, log=print)
            else:
                model_instance = load_chatterbox_model(
                    self.repo_id,
                    self.backend,
                    self.device,
                    multilingual_t3_model=self.multilingual_t3_model,
                )
            if model_instance is None:
                raise RuntimeError("Model backend loader returned no model instance.")
            model_to = getattr(model_instance, "to", None)
            if callable(model_to):
                model_to(self.device)
            model_device = getattr(model_instance, "device", "N/A")
            print(
                f"Model loaded successfully from repo '{self.repo_id}' "
                f"using backend '{self.backend}'. Model device: "
                f"{model_device}"
            )
            self.model_loaded.emit(model_instance, self.device)
        except UnsupportedChatterboxRepoError as e:
            print(
                f"Unsupported model repo '{self.repo_id}': {e}"
            )
            self.error_occurred.emit(str(e))
        except Exception as e:
            tb_str = traceback.format_exc()
            print(
                f"Error loading model repo '{self.repo_id}': {e}\nTraceback:\n{tb_str}")
            self.error_occurred.emit(
                f"Failed to load model repo '{self.repo_id}': {str(e)}\nSee console for traceback.")

# --- AudioGeneratorThread (Same as your working version with stop flag) ---


class _ThreadQuiet:
    """A stream that drops what one thread writes and passes everything else through."""

    def __init__(self, stream, thread_id):
        self._stream, self._thread_id = stream, thread_id

    def write(self, text):
        if threading.get_ident() == self._thread_id:
            return len(text)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.contextmanager
def quiet_this_thread():
    """Hide a model's console chatter while it generates, without swallowing what other
    threads print meanwhile (contextlib.redirect_stdout is process-wide, so it hid the UI's
    "Stop requested" message during long batches)."""
    saved = sys.stdout, sys.stderr
    sys.stdout = _ThreadQuiet(saved[0], threading.get_ident())
    sys.stderr = _ThreadQuiet(saved[1], threading.get_ident())
    try:
        yield
    finally:
        sys.stdout, sys.stderr = saved


class AudioGeneratorThread(QThread):
    generation_complete = Signal(str, int)
    error_occurred = Signal(str)
    chunk_generated = Signal(int, int, int)  # first, last, total
    section_timed = Signal(int, int, float)  # characters (longest in a batch), sections, seconds

    def __init__(
        self,
        model,
        text,
        audio_prompt_path,
        exaggeration,
        temperature,
        cfg_weight,
        seed,
        output_dir,
        language_id="en",
        repetition_penalty=1.2,
        min_p=0.05,
        top_p=1.0,
        finishing=None,
        output_name=None,
        preview=False,
    ):
        super().__init__()
        self.finishing = finishing or audio_effects.FinishingSettings()
        self.output_name = output_name
        self.preview = preview
        self.partial_info = None
        self.subtitle_path = None
        self.pronunciations = None  # set by the app; respells what is spoken
        self.preview_chars = DEFAULT_PREVIEW_CHARS  # set by the app; None previews all the text
        self.model = model
        self.original_text = text
        self.audio_prompt_path = audio_prompt_path
        self.exaggeration = exaggeration
        self.temperature = temperature
        self.cfg_weight = cfg_weight
        self.input_seed = seed
        self.output_dir = output_dir
        self.language_id = language_id
        self.repetition_penalty = repetition_penalty
        self.min_p = min_p
        self.top_p = top_p
        self.actual_seed_used = seed
        self._is_stopped = False
        self.main_voice_prompt_path = None # For V0 / Default
        self.voice_prompt_1_path = None    # For V1
        self.voice_prompt_2_path = None    # For V2
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def stop(self):
        print("Stop requested for audio generation thread (it stops after the sections in progress).")
        self._is_stopped = True
        self.stop_requested_at = time.monotonic()

    def set_seed_internal(self, seed_val: int):
        torch.manual_seed(seed_val)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed_val)  # Corrected from manual_seed
        random.seed(seed_val)
        np.random.seed(seed_val)
        print(f"Seed set to: {seed_val}")
        self.actual_seed_used = seed_val

    def run(self):
        try:
            if nltk is None or not NLTK_RESOURCES_OK:
                self.error_occurred.emit(
                    "NLTK or 'punkt' missing. Check setup.")
                return
            if self.model is None:
                self.error_occurred.emit("Model not loaded.")
                return
            if self._is_stopped:
                self.error_occurred.emit("Stopped by user before start.")
                return

            if self.input_seed == 0:
                r_seed = random.randint(1, 1_000_000)
                self.set_seed_internal(r_seed)
                print(
                    f"Input seed 0. Using random seed: {self.actual_seed_used}")
            else:
                self.set_seed_internal(self.input_seed)

            max_chars = getattr(self.model, "max_section_chars", MAX_TEXT_INPUT_LENGTH)
            planner = getattr(self.model, "plan_sections", None)
            planned = planner(self.original_text) if planner else documents.plan_sections(self.original_text, max_chars)
            if self.preview:
                planned = planned[:preview_cut([len(section.text) for section in planned], self.preview_chars)]
            final_chunks = [section.text for section in planned]
            self.section_boundaries = [section.boundary for section in planned]
            # What the model is given: the pronunciation dictionary applied. final_chunks
            # keeps the written text for subtitles.
            spoken_chunks = list(final_chunks)
            if self.pronunciations is not None:
                respelled = [self.pronunciations.apply_section(chunk) for chunk in final_chunks]
                spoken_chunks = [chunk for chunk, _count in respelled]
                replaced = sum(count for _chunk, count in respelled)
                if replaced:
                    print(f"Pronunciation dictionary: respelled {replaced} word(s).")
            print(f"Split into {len(planned)} sections (up to {max_chars} characters, "
                  "never across paragraphs).")

            if not final_chunks:
                self.error_occurred.emit(
                    "Input text empty or resulted in no chunks.")
                return

            total_chunks = len(final_chunks)
            print(f"Processed into {total_chunks} chunks.")
            # (Optional debug print for chunks can go here)

            all_audio_tensors = []
            sr = self.model.sr
            # Engines that support it (Qwen) generate several sections per call.
            batch_size = max(1, int(getattr(self.model, "batch_size", 1)))
            batches = documents.plan_batches(
                [len(text) for text in final_chunks], batch_size,
                getattr(self.model, "batch_char_budget", None))
            generate_kwargs = dict(
                audio_prompt_path=self.audio_prompt_path if self.audio_prompt_path else None,
                exaggeration=self.exaggeration,
                temperature=self.temperature,
                cfg_weight=self.cfg_weight,
                language_id=self.language_id,
                repetition_penalty=self.repetition_penalty,
                min_p=self.min_p,
                top_p=self.top_p,
            )
            for i, batch_end in batches:
                if self._is_stopped:
                    if all_audio_tensors and not self.preview:
                        # Keep the finished sections of a long render.
                        self.partial_info = (i, total_chunks)
                        skipped = f"section {i + 1}" if i + 1 == total_chunks else f"sections {i + 1}-{total_chunks}"
                        print(f"Stopped by request: saving the {i} finished sections; {skipped} of "
                              f"{total_chunks} weren't generated.")
                        break
                    self.error_occurred.emit(
                        f"Generation stopped by user at chunk {i+1}/{total_chunks}.")
                    return
                batch = spoken_chunks[i:batch_end]
                current_chunk_num = i + 1
                last = i + len(batch)
                self.chunk_generated.emit(current_chunk_num, last, total_chunks)
                span = f"{current_chunk_num}" if len(batch) == 1 else f"{current_chunk_num}-{last}"
                print(f"\nGenerating section {span}/{total_chunks} (seed: {self.actual_seed_used}).")
                section_started = time.monotonic()
                with quiet_this_thread():
                    if len(batch) > 1:
                        wav_tensors = self.model.generate_batch(batch, **generate_kwargs)
                    else:
                        wav_tensors = [self.model.generate(batch[0], **generate_kwargs)]
                for wav_tensor_chunk in wav_tensors:
                    if wav_tensor_chunk.ndim == 1:
                        wav_tensor_chunk = wav_tensor_chunk.unsqueeze(0)
                    all_audio_tensors.append(wav_tensor_chunk.cpu())
                # A batch takes as long as its longest section, so time is measured against that.
                self.section_timed.emit(max(len(text) for text in batch) if len(batch) > 1 else len(batch[0]),
                                        len(batch), time.monotonic() - section_started)

            if self._is_stopped and self.partial_info is None and len(all_audio_tensors) < total_chunks:
                self.error_occurred.emit("Stopped before final concat.")
                return
            if not all_audio_tensors:
                self.error_occurred.emit("No audio data generated.")
                return

            print("\nConcatenating audio chunks...")
            finishing = self.finishing
            sections = [chunk.reshape(-1).float().numpy() for chunk in all_audio_tensors]
            spans, timing = [], {}
            joined_audio = audio_effects.join_sections(
                sections, sr, self.section_boundaries[:len(sections)], finishing.paragraph_pause, spans)
            print(f"Applying finishing touches: {finishing.summary()}")
            final_audio = audio_effects.apply_finishing(joined_audio, sr, finishing, timing=timing)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            if self.preview:
                output_dir = os.path.join(self.output_dir, "previews")
                os.makedirs(output_dir, exist_ok=True)
                file_stem = f"preview_{timestamp}_seed{self.actual_seed_used}"
            else:
                output_dir = self.output_dir
                # Kept as "chatterbox" for now so scripts that look for chatterbox_*.wav keep
                # working; renaming it (and chatterbox_outputs/) is a future migration.
                prefix = self.output_name or "chatterbox"
                suffix = "_partial" if self.partial_info else (
                    "" if self.output_name else "_full_stitched")
                file_stem = f"{prefix}_{timestamp}_seed{self.actual_seed_used}{suffix}"
            output_base = os.path.join(output_dir, file_stem)
            output_path = audio_effects.save_audio(
                output_base, final_audio, sr, finishing.output_format)
            print(f"Final stitched audio saved to: {output_path}")
            if finishing.save_subtitles and not self.preview:
                try:
                    cues = subtitles.build_cues(final_chunks[:len(sections)], spans, joined_audio, sr,
                                                timing.get("scale", 1.0), timing.get("offset", 0.0),
                                                len(final_audio) / sr)
                    self.subtitle_path = subtitles.save(output_base, cues, finishing.subtitle_format)
                    print(f"Subtitles saved to: {self.subtitle_path} ({len(cues)} captions)")
                except Exception as exc:  # subtitles never cost the audio
                    print(f"Could not write subtitles: {safe_console_text(exc)}")
            self.generation_complete.emit(output_path, sr)
        except Exception as e:
            if not self._is_stopped:
                tb_str = traceback.format_exc()
                print(
                    "Error in AudioGeneratorThread: "
                    f"{safe_console_text(e)}\n{safe_console_text(tb_str)}"
                )
                self.error_occurred.emit(
                    f"Generation/stitching error: {str(e)}")


class TaskThread(QThread):
    """Runs one function off the UI thread and reports (result, error message)."""

    done = Signal(object, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.done.emit(self.function(), "")
        except Exception as exc:
            self.done.emit(None, str(exc))


class TranscribeThread(QThread):
    """Runs Whisper off the UI thread."""

    done = Signal(object, str)  # Transcript or None, error

    def __init__(self, transcriber, source, language, task, parent=None):
        super().__init__(parent)
        self.transcriber, self.source, self.language, self.task = transcriber, source, language, task

    def run(self):
        try:
            self.done.emit(self.transcriber.transcribe(self.source, self.language, self.task), "")
        except Exception as exc:
            self.done.emit(None, f"{type(exc).__name__}: {exc}")


class SpeakThread(QThread):
    """Speak a short text with the loaded model (for trying respellings)."""

    finished_with = Signal(object, int, str)  # waveform, sample rate, error

    def __init__(self, model, text, kwargs, parent=None):
        super().__init__(parent)
        self.model, self.text, self.kwargs = model, text, kwargs

    def run(self):
        try:
            try:
                wav = self.model.generate(self.text, **self.kwargs)
            except TypeError:
                wav = self.model.generate(self.text)  # older loaders take fewer options
            data = wav.squeeze(0).detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
            self.finished_with.emit(np.asarray(data, dtype=np.float32).reshape(-1), int(self.model.sr), "")
        except Exception as exc:
            self.finished_with.emit(None, 0, str(exc))


class MakeClipThread(QThread):
    """Generates a reference clip with the current voice: a phonetically rich passage, so
    the clip comes with an exact transcript."""

    finished_with = Signal(object, int, str)  # waveform (numpy) or None, sample rate, error

    def __init__(self, model, text, language_id, parent=None):
        super().__init__(parent)
        self.model = model
        self.text = text
        self.language_id = language_id

    def run(self):
        try:
            wav = self.model.generate(self.text, language_id=self.language_id)
            data = wav.squeeze(0).detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
            self.finished_with.emit(np.asarray(data, dtype=np.float32).reshape(-1), int(self.model.sr), "")
        except Exception as exc:
            self.finished_with.emit(None, 0, str(exc))


class EngineInstallThread(QThread):
    finished_with = Signal(str)

    def __init__(self, engine_module, parent=None):
        super().__init__(parent)
        self.engine_module = engine_module

    def run(self):
        try:
            self.engine_module.install(log=print)
            self.finished_with.emit("")
        except Exception as exc:
            self.finished_with.emit(str(exc))
