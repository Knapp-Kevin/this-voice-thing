"""Local HTTP API: lets other programs on this PC use the app's models and voices.

Only listens on 127.0.0.1. Uses the standard library HTTP server, so it adds no
dependencies. The app supplies a backend object with these methods (each may
raise ApiError):

  health() -> dict             models() -> list         voices() -> list
  synthesize(request: dict) -> dict with "path", "mime", plus details
  synthesize_stream(request: dict) -> dict with PCM chunk iterator + stream metadata
  transcribe(audio: bytes, filename, language) -> transcription.Transcript

Endpoints
  GET  /v1/health        what's loaded, and whether a request is running
  GET  /v1/models        the model list (what "model" can name)
  GET  /v1/voices        the voice library and the loaded model's built-in voices
  POST /v1/audio/speech  OpenAI-compatible buffered speech, or native PCM streaming
                         with stream=true, stream_format="audio", response_format="pcm"
  POST /v1/speech        native: {"text", "model", "voice", "format", "subtitles",
                         "speed", "language", "style", "name"} -> JSON with the saved
                         files (chatterbox_outputs/api/) and details
  POST /v1/audio/transcriptions  OpenAI-compatible speech to text: multipart form
                         with "file", optional "language", "response_format"
                         (json, text, srt, vtt, verbose_json); Whisper runs locally
"""

import email.parser
import email.policy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765
MAX_BODY = 4 * 1024 * 1024
MAX_AUDIO_BODY = 100 * 1024 * 1024
TRANSCRIPT_FORMATS = ("json", "text", "srt", "vtt", "verbose_json")
OPENAI_FORMATS = {"wav": "WAV", "flac": "FLAC", "mp3": "MP3"}


class ApiError(Exception):
    def __init__(self, status, message, kind="invalid_request_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.kind = kind


class LocalApiServer:
    def __init__(self, backend, port=DEFAULT_PORT, token="", log=print):
        self.backend = backend
        self.port = int(port)
        self.token = token.strip()
        self.log = log
        self.httpd = None
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        server = self

        class Handler(_Handler):
            api = server

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="local-api", daemon=True)
        self.thread.start()
        self.log(f"Local API listening on {self.url}")

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.log("Local API stopped.")


class _Handler(BaseHTTPRequestHandler):
    api = None  # set per server
    server_version = "ThisVoiceThing/1.1"
    protocol_version = "HTTP/1.1"

    # --- plumbing ---

    def log_message(self, fmt, *args):
        pass  # requests are logged by _route with their outcome

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, error):
        self._send_json(error.status, {"error": {"message": error.message, "type": error.kind}})

    def _send_file(self, path, mime):
        with open(path, "rb") as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, payload):
        if not payload:
            return
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _send_pcm_stream(self, result):
        chunks = iter(result["chunks"])
        try:
            first = next(chunks)
        except StopIteration:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
            raise ApiError(500, "The model completed without producing any audio.", "server_error")
        except Exception:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
            raise

        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Audio-Sample-Rate", str(result["sample_rate"]))
        self.send_header("X-Audio-Sample-Format", result.get("sample_format", "s16le"))
        self.send_header("X-Audio-Channels", str(result.get("channels", 1)))
        self.send_header("X-Streaming-Mode", result.get("streaming", "native"))
        self.send_header("X-Audio-Watermark", result.get("watermark", "unknown"))
        self.end_headers()

        try:
            self._write_chunk(first)
            for chunk in chunks:
                self._write_chunk(chunk)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            # Headers/audio may already be on the wire, so a JSON error response would
            # corrupt the stream. Close it and log the terminal failure instead.
            self.api.log(f"API streaming audio ended early: {type(exc).__name__}: {exc}")
            self.close_connection = True
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()

    def _authorized(self):
        token = self.api.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {token}" or self.headers.get("X-API-Key", "") == token

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request body is too large (4 MB max).")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "The body must be JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "The body must be a JSON object.")
        return data

    def _read_form(self):
        """A multipart/form-data body as ({field: text}, {field: (filename, bytes)})."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_AUDIO_BODY:
            raise ApiError(413, "The upload is too large (100 MB max).")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise ApiError(400, "Send the audio as multipart/form-data with a \"file\" field.")
        return parse_multipart(content_type, self.rfile.read(length) if length else b"")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        status_note = "ok"
        try:
            if not self._authorized():
                raise ApiError(401, "Missing or wrong API token (Authorization: Bearer <token>).",
                               "authentication_error")
            backend = self.api.backend
            if method == "GET" and path in ("/", "/v1", "/v1/health"):
                self._send_json(200, backend.health())
            elif method == "GET" and path == "/v1/models":
                self._send_json(200, {"object": "list", "data": backend.models()})
            elif method == "GET" and path == "/v1/voices":
                self._send_json(200, {"object": "list", "data": backend.voices()})
            elif method == "POST" and path == "/v1/audio/speech":
                self._openai_speech(self._read_json())
            elif method == "POST" and path == "/v1/speech":
                self._native_speech(self._read_json())
            elif method == "POST" and path == "/v1/audio/transcriptions":
                self._transcription(*self._read_form())
            else:
                raise ApiError(404, f"No endpoint {method} {path}. See GET /v1/health.", "not_found")
        except ApiError as error:
            status_note = f"{error.status} {error.message}"
            self._send_error(error)
        except Exception as exc:  # never take the app down
            status_note = f"500 {exc}"
            self._send_error(ApiError(500, f"Internal error: {exc}", "server_error"))
        if path not in ("/v1/health",):
            self.api.log(f"API {method} {path}: {status_note}")

    # --- speech ---

    def _openai_speech(self, data):
        text = str(data.get("input") or "").strip()
        if not text:
            raise ApiError(400, "\"input\" is required.")
        stream = data.get("stream", False)
        if not isinstance(stream, bool):
            raise ApiError(400, "stream must be true or false.")
        response_format = str(data.get("response_format") or ("pcm" if stream else "mp3")).lower()

        if stream:
            stream_format = str(data.get("stream_format") or "").lower()
            if stream_format != "audio":
                raise ApiError(400, 'Native streaming currently requires stream_format="audio".')
            if response_format != "pcm":
                raise ApiError(400, 'Native streaming currently requires response_format="pcm".')
            speed = data.get("speed")
            if speed not in (None, ""):
                try:
                    if abs(float(speed) - 1.0) > 1e-9:
                        raise ApiError(400, "speed must be 1.0 for live PCM streaming.")
                except (TypeError, ValueError):
                    raise ApiError(400, "speed must be a number.")
            result = self.api.backend.synthesize_stream({
                "text": text, "model": data.get("model"), "voice": data.get("voice"),
                "format": "WAV", "speed": 1.0, "style": data.get("instructions"),
                "save": False, "subtitles": None, "live": True,
            })
            self._send_pcm_stream(result)
            return

        if response_format not in OPENAI_FORMATS:
            raise ApiError(400, f"response_format {response_format!r} isn't supported here; "
                                f"use one of {', '.join(OPENAI_FORMATS)}.")
        result = self.api.backend.synthesize({
            "text": text, "model": data.get("model"), "voice": data.get("voice"),
            "format": OPENAI_FORMATS[response_format], "speed": data.get("speed"),
            "style": data.get("instructions"), "save": False, "subtitles": None,
        })
        try:
            self._send_file(result["path"], result["mime"])
        finally:
            _cleanup(result)

    def _native_speech(self, data):
        text = str(data.get("text") or data.get("input") or "").strip()
        if not text:
            raise ApiError(400, "\"text\" is required.")
        result = self.api.backend.synthesize({
            "text": text, "model": data.get("model"), "voice": data.get("voice"),
            "format": str(data.get("format") or "WAV").upper(), "speed": data.get("speed"),
            "language": data.get("language"), "style": data.get("style"),
            "subtitles": data.get("subtitles"), "save": True, "name": data.get("name"),
        })
        self._send_json(200, {key: value for key, value in result.items() if key not in ("mime", "temporary")})


    # --- transcription ---

    def _transcription(self, fields, files):
        if "file" not in files:
            raise ApiError(400, "\"file\" is required (the audio to transcribe).")
        filename, audio = files["file"]
        if not audio:
            raise ApiError(400, "The uploaded file is empty.")
        response_format = (fields.get("response_format") or "json").strip().lower()
        if response_format not in TRANSCRIPT_FORMATS:
            raise ApiError(400, f"response_format must be one of {', '.join(TRANSCRIPT_FORMATS)}.")
        language = (fields.get("language") or "auto").strip().lower() or "auto"
        transcript = self.api.backend.transcribe(audio, filename, language)
        status, body, mime = format_transcript(transcript, response_format)
        if mime == "application/json":
            self._send_json(status, body)
        else:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def parse_multipart(content_type, body):
    """Split a multipart/form-data body into text fields and uploaded files."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        raise ApiError(400, "Couldn't read the multipart form.")
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, data)
        else:
            fields[name] = data.decode(part.get_content_charset() or "utf-8", "replace")
    return fields, files


def format_transcript(transcript, response_format):
    """(status, body, mime) for an OpenAI-style transcription response."""
    from this_voice_thing.core import subtitles, transcription
    if response_format == "text":
        return 200, transcript.text + "\n", "text/plain; charset=utf-8"
    if response_format in ("srt", "vtt"):
        cues = transcription.to_cues(transcript)
        if response_format == "vtt":
            return 200, subtitles.to_vtt(cues), "text/vtt; charset=utf-8"
        return 200, subtitles.to_srt(cues), "text/plain; charset=utf-8"
    if response_format == "verbose_json":
        return 200, {
            "task": transcript.task, "language": transcript.language, "duration": round(transcript.seconds, 2),
            "text": transcript.text,
            "segments": [{"id": index, "start": round(segment.start, 2), "end": round(segment.end, 2),
                          "text": segment.text} for index, segment in enumerate(transcript.segments)],
        }, "application/json"
    return 200, {"text": transcript.text}, "application/json"


def _cleanup(result):
    if result.get("temporary"):
        for key in ("path", "subtitles"):
            path = result.get(key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
