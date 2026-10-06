"""Persistent Live Voice soundboards and content-addressed TTS caches."""

from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import json
import os
import uuid
import wave


SCHEMA_VERSION = 1
SOUNDBOARD_DIRNAME = "soundboard"
INDEX_FILENAME = "boards.json"


@dataclass
class Pad:
    label: str
    text: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = "tts"
    voice_id: str = ""
    model_repo_id: str = ""
    model_backend: str = ""
    model_mode: str = ""
    language: str = "en"
    style: str = ""
    temperature: float = 0.8
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    repetition_penalty: float = 1.2
    min_p: float = 0.05
    top_p: float = 1.0
    interrupt_policy: str = "queue"
    cache_policy: str = "auto"
    cache_key: str = ""
    hotkey: str = ""
    tags: list = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))


@dataclass
class Board:
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    pads: list = field(default_factory=list)
    default_voice_id: str = ""
    route_profile: str = ""


def voice_fingerprint(voice, clip_path="", transcript=""):
    """Stable digest of a voice definition plus the referenced clip revision."""
    if voice is None:
        payload = {"voice": None}
    else:
        payload = {"voice": asdict(voice)}
    if clip_path and os.path.isfile(clip_path):
        try:
            stat = os.stat(clip_path)
            payload["clip"] = {
                "path": os.path.abspath(clip_path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        except OSError:
            payload["clip"] = {"path": os.path.abspath(clip_path), "missing": True}
    payload["transcript"] = str(transcript or "")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SoundboardStore:
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.dir = os.path.join(app_dir, SOUNDBOARD_DIRNAME)
        self.cache_dir = os.path.join(self.dir, "cache")
        self.index_path = os.path.join(self.dir, INDEX_FILENAME)
        self.boards = []
        self.active_board_id = ""
        self.load()
        if not self.boards:
            board = Board(name="Main")
            self.boards = [board]
            self.active_board_id = board.id
            self.save()

    def load(self):
        try:
            with open(self.index_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        self.boards = []
        pad_fields = Pad.__dataclass_fields__
        board_fields = Board.__dataclass_fields__
        for raw in data.get("boards", []):
            if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
                continue
            pads = []
            for item in raw.get("pads", []):
                if not isinstance(item, dict) or not str(item.get("label", "")).strip():
                    continue
                filtered = {key: value for key, value in item.items() if key in pad_fields}
                if filtered.get("kind", "tts") != "tts":
                    continue
                pads.append(Pad(**filtered))
            values = {key: value for key, value in raw.items() if key in board_fields and key != "pads"}
            values["pads"] = pads
            self.boards.append(Board(**values))
        requested = str(data.get("active_board_id") or "")
        self.active_board_id = requested if any(b.id == requested for b in self.boards) else (
            self.boards[0].id if self.boards else ""
        )

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "active_board_id": self.active_board_id,
            "boards": [
                dict(asdict(board), pads=[asdict(pad) for pad in board.pads])
                for board in self.boards
            ],
        }
        temp = self.index_path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, self.index_path)

    def active_board(self):
        board = next((board for board in self.boards if board.id == self.active_board_id), None)
        if board is None and self.boards:
            board = self.boards[0]
            self.active_board_id = board.id
        return board

    def get_pad(self, pad_id):
        for board in self.boards:
            for pad in board.pads:
                if pad.id == pad_id:
                    return pad
        return None

    def add_pad(self, pad, board=None):
        board = board or self.active_board()
        if board is None:
            board = Board(name="Main")
            self.boards.append(board)
            self.active_board_id = board.id
        existing = {item.label.lower() for item in board.pads}
        base = pad.label.strip() or "Phrase"
        label = base
        number = 2
        while label.lower() in existing:
            label = f"{base} {number}"
            number += 1
        pad.label = label
        board.pads.append(pad)
        self.save()
        return pad

    def remove_pad(self, pad_id):
        removed = None
        for board in self.boards:
            kept = []
            for pad in board.pads:
                if pad.id == pad_id:
                    removed = pad
                else:
                    kept.append(pad)
            board.pads = kept
        self.save()
        if removed is not None:
            self.garbage_collect_cache()
        return removed

    @staticmethod
    def cache_digest(pad, synthesis_context):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "pad": {
                "kind": pad.kind,
                "text": pad.text,
                "voice_id": pad.voice_id,
                "model_repo_id": pad.model_repo_id,
                "model_backend": pad.model_backend,
                "model_mode": pad.model_mode,
                "language": pad.language,
                "style": pad.style,
                "temperature": pad.temperature,
                "exaggeration": pad.exaggeration,
                "cfg_weight": pad.cfg_weight,
                "repetition_penalty": pad.repetition_penalty,
                "min_p": pad.min_p,
                "top_p": pad.top_p,
            },
            "synthesis": synthesis_context,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def cache_path(self, key):
        return os.path.join(self.cache_dir, f"{key}.wav")

    def has_cache(self, key):
        return bool(key) and os.path.isfile(self.cache_path(key))

    def write_pcm_cache(self, key, pcm, sample_rate):
        if not key:
            raise ValueError("A soundboard cache key is required.")
        os.makedirs(self.cache_dir, exist_ok=True)
        path = self.cache_path(key)
        temp = path + ".tmp"
        with wave.open(temp, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            handle.writeframes(bytes(pcm))
        os.replace(temp, path)
        return path

    def garbage_collect_cache(self):
        if not os.path.isdir(self.cache_dir):
            return 0
        referenced = {pad.cache_key for board in self.boards for pad in board.pads if pad.cache_key}
        removed = 0
        for name in os.listdir(self.cache_dir):
            if not name.lower().endswith(".wav"):
                continue
            key = os.path.splitext(name)[0]
            if key in referenced:
                continue
            try:
                os.remove(os.path.join(self.cache_dir, name))
                removed += 1
            except OSError:
                pass
        return removed
