"""Runs local API requests on the app: the HTTP threads call in, the UI thread does the work."""

import os
import tempfile
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal

from this_voice_thing.core import transcription
from this_voice_thing.integrations import local_api
from this_voice_thing.ui.threads import AudioGeneratorThread


class ApiBridge(QObject):
    """The local API's backend. HTTP requests arrive on server threads; anything that
    touches the app's state runs on the UI thread, and generation itself runs on
    the request's thread through the same generator the Generate button uses."""

    run_requested = Signal(object)
    OPENAI_MODEL_NAMES = {"", "tts-1", "tts-1-hd", "gpt-4o-mini-tts", "default"}
    LOAD_TIMEOUT = 900

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.job_lock = threading.Lock()  # one API generation at a time
        self.run_requested.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, call):
        function, box, done = call
        try:
            box["value"] = function()
        except Exception as exc:
            box["error"] = exc
        done.set()

    def on_ui(self, function, timeout=30):
        box, done = {}, threading.Event()
        self.run_requested.emit((function, box, done))
        if not done.wait(timeout):
            raise local_api.ApiError(503, "The app didn't respond in time.", "server_error")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def health(self):
        return self.on_ui(self.app.api_health)

    def models(self):
        return self.on_ui(self.app.api_models)

    def voices(self):
        return self.on_ui(self.app.api_voices)

    def _begin_job(self, request):
        deadline = time.monotonic() + self.LOAD_TIMEOUT
        while True:
            job = self.on_ui(lambda: self.app.api_begin(request))
            if not job.get("loading"):
                return job
            if time.monotonic() > deadline:
                raise local_api.ApiError(504, "The model took too long to load.", "server_error")
            time.sleep(0.5)

    def synthesize(self, request):
        if not self.job_lock.acquire(timeout=self.LOAD_TIMEOUT):
            raise local_api.ApiError(503, "Another API request is still running.", "server_error")
        try:
            job = self._begin_job(request)
            try:
                return self._generate(job)
            finally:
                self.on_ui(self.app.api_end)
        finally:
            self.job_lock.release()

    def synthesize_stream(self, request):
        """Prepare one native streaming synthesis session.

        The GPU/API lock remains held until the returned chunk iterator is exhausted
        or closed. For this first live slice only engines that expose a genuine
        generate_streaming_pcm method are eligible.
        """
        if not self.job_lock.acquire(timeout=self.LOAD_TIMEOUT):
            raise local_api.ApiError(503, "Another API request is still running.", "server_error")
        job = None
        try:
            job = self._begin_job(request)
            generator = job["generator"]
            model = generator["model"]
            stream_method = getattr(model, "generate_streaming_pcm", None)
            if not callable(stream_method) or not getattr(model, "native_streaming", False):
                raise local_api.ApiError(
                    409,
                    "The selected model does not support native live audio streaming yet. "
                    "Use VoxCPM2, or omit stream for normal buffered synthesis.",
                )
            text = generator["text"]
            pronunciations = job.get("pronunciations")
            if pronunciations is not None:
                text, _count = pronunciations.apply_section(text)
            source = stream_method(text, audio_prompt_path=generator.get("audio_prompt_path"))

            def chunks():
                try:
                    yield from source
                finally:
                    close = getattr(source, "close", None)
                    if close is not None:
                        close()
                    try:
                        self.on_ui(self.app.api_end)
                    finally:
                        self.job_lock.release()

            return {
                "chunks": chunks(),
                "sample_rate": int(getattr(model, "sr", 48000)),
                "channels": 1,
                "sample_format": "s16le",
                "streaming": "native",
                "watermark": "not-applied-live-path",
                "model": job["model_label"],
                "voice": job["voice_label"],
            }
        except Exception:
            if job is not None:
                try:
                    self.on_ui(self.app.api_end)
                except Exception:
                    pass
            self.job_lock.release()
            raise

    def transcribe(self, audio, filename, language):
        if not transcription.is_downloaded():
            raise local_api.ApiError(409, "Whisper isn't downloaded yet. Transcribe something once on the app's "
                                          "Transcribe page to download it (about 1.6 GB).")
        if language != "auto" and language not in transcription.LANGUAGES:
            raise local_api.ApiError(400, f"Unknown language {language!r}; use a code such as en, es or ja, or auto.")
        suffix = os.path.splitext(filename or "")[1].lower() or ".wav"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            handle.write(audio)
            handle.close()
            try:
                return self.app.transcriber.transcribe(handle.name, language)
            except Exception as exc:
                raise local_api.ApiError(400, f"Couldn't transcribe that audio: {type(exc).__name__}: {exc}")
        finally:
            try:
                os.remove(handle.name)
            except OSError:
                pass

    def _generate(self, job):
        outcome = {}
        thread = AudioGeneratorThread(**job["generator"])
        thread.pronunciations = job["pronunciations"]
        thread.generation_complete.connect(
            lambda path, sr: outcome.update(path=path, sr=sr), Qt.ConnectionType.DirectConnection)
        thread.error_occurred.connect(
            lambda message: outcome.update(error=message), Qt.ConnectionType.DirectConnection)
        started = time.monotonic()
        thread.run()  # in this request's thread, not a new one
        if "path" not in outcome:
            raise local_api.ApiError(500, outcome.get("error", "Generation failed."), "server_error")
        import soundfile
        info = soundfile.info(outcome["path"])
        return {
            "path": outcome["path"], "subtitles": thread.subtitle_path,
            "seconds": round(info.duration, 2), "sample_rate": outcome["sr"],
            "generation_seconds": round(time.monotonic() - started, 2),
            "model": job["model_label"], "voice": job["voice_label"],
            "mime": job["mime"], "temporary": job["temporary"], "seed": thread.actual_seed_used,
        }
