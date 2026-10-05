"""The voice library: named voices saved for reuse across models.

Three kinds of voice:
  clip    a reference clip (and its transcript) that every cloning model can use
  preset  a built-in speaker of one model (Kokoro voice, Qwen speaker + style)
  design  a description for a voice-design model (Qwen/VoxCPM text, OmniVoice attributes)

The index lives in voice_library/voices.json. Clips made by the library are stored
in voice_library/clips/; recordings stay in reference_recordings/ and are only
referenced. A clip's transcript is the .txt file beside it, the same convention
the recorder uses, so it is shared with the rest of the app.
"""

import datetime
import json
import os
import re
import uuid
import wave
from dataclasses import asdict, dataclass, field

LIBRARY_DIRNAME = "voice_library"
INDEX_FILENAME = "voices.json"
KINDS = {"clip": "Clip", "preset": "Preset", "design": "Designed"}


@dataclass
class Voice:
    name: str
    kind: str                     # "clip" | "preset" | "design"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    tags: list = field(default_factory=list)
    notes: str = ""
    created: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))
    clip: str = ""                # audio file (relative to the app folder when inside it)
    backend: str = ""             # preset/design: the engine
    repo_id: str = ""             # preset/design: the model
    mode: str = ""                # design entries of dual-mode engines
    speaker: str = ""             # preset: built-in speaker / voice id
    style: str = ""               # preset (Qwen): style instruction
    description: str = ""         # design: the description or attributes
    language: str = ""
    origin: str = ""              # Studio: "recorded" | "cloned" | "designed" | "remixed"

    @property
    def has_clip(self):
        return bool(self.clip)


def transcript_path(audio_path):
    return os.path.splitext(audio_path)[0] + ".txt"


def read_transcript(audio_path):
    try:
        with open(transcript_path(audio_path), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def write_transcript(audio_path, text):
    path = transcript_path(audio_path)
    if text.strip():
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.strip() + "\n")
    elif os.path.exists(path):
        os.remove(path)


def clip_seconds(path):
    try:
        with wave.open(path, "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except Exception:
        try:
            import soundfile
            return soundfile.info(path).duration
        except Exception:
            return 0.0


def slug(name):
    text = re.sub(r"[^\w\-]+", "_", name.strip()).strip("_")
    return text[:40] or "voice"


class VoiceLibrary:
    def __init__(self, app_dir, recordings_dir):
        self.app_dir = app_dir
        self.dir = os.path.join(app_dir, LIBRARY_DIRNAME)
        self.clips_dir = os.path.join(self.dir, "clips")
        self.index_path = os.path.join(self.dir, INDEX_FILENAME)
        self.recordings_dir = recordings_dir
        self.voices = []
        self.dismissed = []  # recordings removed from the library (not re-imported)
        self.load()

    # --- paths ---

    def to_stored(self, path):
        """Paths inside the app folder are stored relative, so the folder can move."""
        path = os.path.abspath(path)
        try:
            relative = os.path.relpath(path, self.app_dir)
        except ValueError:  # another drive
            return path
        return path if relative.startswith("..") else relative.replace("\\", "/")

    def clip_path(self, voice):
        if not voice.clip:
            return ""
        return voice.clip if os.path.isabs(voice.clip) else os.path.join(self.app_dir, voice.clip)

    # --- storage ---

    def load(self):
        self.voices, self.dismissed = [], []
        try:
            with open(self.index_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        known = Voice.__dataclass_fields__
        for item in data.get("voices", []):
            if isinstance(item, dict) and item.get("name") and item.get("kind") in KINDS:
                self.voices.append(Voice(**{key: value for key, value in item.items() if key in known}))
        self.dismissed = [path for path in data.get("dismissed", []) if isinstance(path, str)]

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        payload = {"voices": [asdict(voice) for voice in self.voices], "dismissed": self.dismissed}
        temp = self.index_path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, self.index_path)

    # --- queries ---

    def get(self, voice_id):
        return next((voice for voice in self.voices if voice.id == voice_id), None)

    def find_clip(self, path):
        stored = self.to_stored(path)
        return next((voice for voice in self.voices if voice.clip == stored), None)

    def unique_name(self, name, ignore=None):
        names = {voice.name.lower() for voice in self.voices if voice is not ignore}
        candidate, number = name, 2
        while candidate.lower() in names:
            candidate = f"{name} {number}"
            number += 1
        return candidate

    def clip_voices(self):
        return [voice for voice in self.voices if voice.has_clip and os.path.exists(self.clip_path(voice))]

    # --- changes ---

    def add(self, voice):
        voice.name = self.unique_name(voice.name)
        self.voices.append(voice)
        self.save()
        return voice

    def add_clip(self, path, name=None, tags=None):
        """Add (or return) the library voice for an audio file, which stays where it is."""
        existing = self.find_clip(path)
        if existing:
            return existing
        stored = self.to_stored(path)
        if stored in self.dismissed:
            self.dismissed.remove(stored)
        voice = Voice(name=name or os.path.splitext(os.path.basename(path))[0], kind="clip",
                      clip=stored, tags=list(tags or []))
        return self.add(voice)

    def remove(self, voice, delete_file=False):
        self.voices = [item for item in self.voices if item is not voice]
        path = self.clip_path(voice)
        owned = path and os.path.abspath(path).startswith(os.path.abspath(self.clips_dir))
        if path and (delete_file or owned):
            for target in (path, transcript_path(path)):
                if os.path.exists(target):
                    os.remove(target)
        elif voice.clip and voice.clip not in self.dismissed:
            self.dismissed.append(voice.clip)  # don't import this recording again
        self.save()

    def new_clip_path(self, name):
        os.makedirs(self.clips_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self.clips_dir, f"{slug(name)}_{stamp}.wav")

    def import_recordings(self):
        """Add recordings that aren't in the library yet. Returns how many were added."""
        if not os.path.isdir(self.recordings_dir):
            return 0
        added = 0
        for name in sorted(os.listdir(self.recordings_dir)):
            if not name.lower().endswith(".wav"):
                continue
            path = os.path.join(self.recordings_dir, name)
            stored = self.to_stored(path)
            if stored in self.dismissed or self.find_clip(path):
                continue
            when = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            voice = Voice(name=f"Recording {when.strftime('%b %d, %I:%M %p').replace(' 0', ' ')}",
                          kind="clip", clip=stored, tags=["recording"],
                          created=when.isoformat(timespec="seconds"))
            voice.name = self.unique_name(voice.name)
            self.voices.append(voice)
            added += 1
        if added:
            self.save()
        return added
