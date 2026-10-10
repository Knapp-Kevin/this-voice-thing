"""Engine definitions, models.json persistence and Hugging Face lookups.

Engine-neutral by design: each models.json entry names an engine through its
"backend" key, and each engine declares which fields it uses. New engines
(e.g. Qwen3-TTS) are added here without changing the entry format.
"""

from dataclasses import dataclass
import json
import os
import re

from huggingface_hub import HfApi, hf_hub_download, scan_cache_dir, try_to_load_from_cache
from huggingface_hub.errors import (
    GatedRepoError, HfHubHTTPError, RepositoryNotFoundError)

ENTRY_KEYS = (
    "repo_id", "label", "enabled", "experimental", "test_text", "test_texts",
    "notes", "backend", "language_id", "multilingual_t3_model", "qwen_variant",
    "license", "download_bytes", "mode",
)

# ---------- licenses ----------

# Licenses of the models the app ships with, for entries saved before licenses were recorded.
KNOWN_LICENSES = {"ResembleAI/chatterbox": "mit", "Qwen/": "apache-2.0", "hexgrad/": "apache-2.0",
                  "openbmb/": "apache-2.0"}
PERMISSIVE_LICENSES = {
    "mit": "MIT", "apache-2.0": "Apache 2.0", "bsd-2-clause": "BSD", "bsd-3-clause": "BSD",
    "cc-by-4.0": "CC BY 4.0", "cc0-1.0": "CC0", "unlicense": "Unlicense", "mpl-2.0": "MPL 2.0",
    "openrail": "OpenRAIL", "openrail++": "OpenRAIL", "creativeml-openrail-m": "OpenRAIL",
}


# Weights whose real license is stricter than their Hub tags say (or than a missing tag
# suggests). Fine-tunes and conversions inherit it: they can't loosen the base model's terms.
WEIGHT_LICENSES = {
    # Code Apache-2.0, weights CC BY-NC because of the Emilia training data (model card).
    "k2-fsa/OmniVoice": "cc-by-nc-4.0",
}


# Engines whose every public checkpoint derives from non-commercial weights, so any repo
# with that layout is treated as non-commercial whatever its tag says.
ENGINE_WEIGHT_LICENSES = {
    "omnivoice": "cc-by-nc-4.0",
    # MIT-licensed, but Microsoft's model card limits VibeVoice to research use and
    # recommends against commercial use; shown as "Research use", not a green MIT badge.
    "vibevoice": "mit-research",
}


def resolve_license(repo_id, tags=None, declared="", backend=None):
    """The license that actually applies: a known base model's terms override the tag."""
    if backend in ENGINE_WEIGHT_LICENSES:
        return ENGINE_WEIGHT_LICENSES[backend]
    repo = (repo_id or "").lower()
    bases = [tag.lower().split(":")[-1] for tag in tags or [] if tag.lower().startswith("base_model:")]
    for base, license_id in WEIGHT_LICENSES.items():
        if repo == base.lower() or base.lower() in bases:
            return license_id
    return (declared or "").lower()


def license_from_tags(tags):
    for tag in tags or []:
        if tag.lower().startswith("license:"):
            return tag.split(":", 1)[1].lower()
    return ""


def license_of(entry):
    license_id = resolve_license(entry.get("repo_id"), declared=entry.get("license") or "",
                                 backend=entry.get("backend"))
    if not license_id:
        for prefix, known in KNOWN_LICENSES.items():
            if entry.get("repo_id", "").startswith(prefix):
                return known
    return license_id


def license_badge(license_id):
    """(kind, label): kind is 'permissive', 'noncommercial' or 'unknown'."""
    license_id = (license_id or "").lower()
    if license_id in PERMISSIVE_LICENSES:
        return "permissive", PERMISSIVE_LICENSES[license_id]
    if ("nc" in license_id.split("-") or "non-commercial" in license_id
            or "cpml" in license_id or "coqui" in license_id):
        return "noncommercial", "Non-commercial"
    if license_id == "mit-research":
        return "unknown", "Research use"
    if not license_id or license_id == "other":
        return "unknown", "License ?"
    return "unknown", "License ?"


@dataclass(frozen=True)
class Engine:
    key: str                 # value stored in models.json "backend"
    label: str
    description: str
    languages_summary: str
    uses_weights_version: bool = False


ENGINES = {
    "multilingual": Engine(
        key="multilingual",
        label="Chatterbox multilingual",
        description="23 languages, voice cloning from a reference clip.",
        languages_summary="23 languages",
        uses_weights_version=True,
    ),
    "legacy": Engine(
        key="legacy",
        label="Chatterbox original",
        description="The original single-language Chatterbox layout: English for the official "
                    "weights, or the language a community fine-tune was trained on.",
        languages_summary="single language",
    ),
    "qwen3": Engine(
        key="qwen3",
        label="Qwen3-TTS",
        description="10 languages; preset voices with style instructions, voice design from a "
                    "description, or cloning. Runs in its own environment (Apache-2.0).",
        languages_summary="10 languages",
    ),
    "kokoro": Engine(
        key="kokoro",
        label="Kokoro",
        description="A small, very fast model (82M parameters) with dozens of built-in voices in "
                    "7 languages. Runs in its own environment (Apache-2.0).",
        languages_summary="7 languages",
    ),
    "voxcpm": Engine(
        key="voxcpm",
        label="VoxCPM2",
        description="One 2B-parameter model for voice cloning (with optional style and a clip "
                    "transcript for closer likeness) and voice design from a description. 30 "
                    "languages, 48 kHz output. Runs in its own environment (Apache-2.0).",
        languages_summary="30 languages",
    ),
    "vibevoice": Engine(
        key="vibevoice",
        label="VibeVoice",
        description="Microsoft's model for natural conversations: a script with up to 4 speakers, "
                    "each cloned from a clip. MIT license, but the model card limits it to research "
                    "use. Runs in its own environment.",
        languages_summary="English and Chinese",
    ),
    "omnivoice": Engine(
        key="omnivoice",
        label="OmniVoice",
        description="A small, fast model for voice cloning and voice design in 600+ languages. "
                    "Non-commercial: the weights are CC BY-NC 4.0. Runs in its own environment.",
        languages_summary="600+ languages",
    ),
}
VOICE_MODES = {"clone": "Voice cloning", "design": "Voice design"}
# Engines where one model both clones and designs voices; each entry picks one "mode".
DUAL_MODE_ENGINES = {"voxcpm", "omnivoice"}


def entry_mode(entry):
    """'clone' or 'design' for a dual-mode entry ('voxcpm_mode' is the older key)."""
    mode = entry.get("mode") or entry.get("voxcpm_mode")
    return "design" if mode == "design" else "clone"

QWEN_VARIANTS = {
    "custom_voice": "Preset voices",
    "voice_design": "Voice design",
    "base": "Voice cloning",
}

WEIGHT_VERSIONS = {
    "v3": "t3_mtl23ls_v3.safetensors",
    "v2": "t3_mtl23ls_v2.safetensors",
}

# Files each engine downloads (mirrors model_backends / chatterbox loaders).
ENGINE_FILES = {
    "multilingual": ["ve.pt", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json",
                     "conds.pt", "Cangjie5_TC.json"],
    "legacy": ["ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
               "tokenizer.json", "conds.pt"],
}
REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][\w.\-]*/[\w.\-]+$")


def engine_for(entry):
    return ENGINES.get(entry.get("backend", "multilingual"), ENGINES["multilingual"])


def weights_file(entry):
    version = str(entry.get("multilingual_t3_model") or "v3")
    if version.endswith(".safetensors"):
        return version
    return WEIGHT_VERSIONS.get(version.lower(), WEIGHT_VERSIONS["v3"])


def key_weight_file(entry):
    """The large file whose presence means the entry's model is downloaded."""
    if entry.get("backend") == "legacy":
        return "t3_cfg.safetensors"
    if entry.get("backend") in ("qwen3", "voxcpm", "omnivoice"):
        return "model.safetensors"
    if entry.get("backend") == "vibevoice":
        return "model.safetensors.index.json"  # plus its shards, checked in is_downloaded
    if entry.get("backend") == "kokoro":
        return "config.json"  # plus a .pth, checked in is_downloaded
    return weights_file(entry)


# What each model is for. The order is the order groups appear in the UI.
CAPABILITIES = {
    "clone": ("Voice cloning", "Speak in the voice of a reference clip from the Voice page."),
    "preset": ("Preset voices", "Pick a built-in speaker; some models also take a style."),
    "design": ("Voice design", "Describe a voice in words and the model creates it."),
    "conversation": ("Conversations", "Scripts with up to 4 speakers, each in their own voice."),
}
# Short labels for the Model page tabs, so four tabs fit the narrowest window.
CAPABILITY_TABS = {"clone": "Cloning", "preset": "Presets", "design": "Design", "conversation": "Conversations"}
QWEN_CAPABILITY = {"base": "clone", "custom_voice": "preset", "voice_design": "design"}


def capability_for(entry):
    if entry.get("backend") == "qwen3":
        return QWEN_CAPABILITY.get(entry.get("qwen_variant"), "clone")
    if entry.get("backend") == "kokoro":
        return "preset"
    if entry.get("backend") in DUAL_MODE_ENGINES:
        return entry_mode(entry)
    if entry.get("backend") == "vibevoice":
        return "conversation"
    return "clone"  # Chatterbox clones, or uses its built-in voice with no clip


def live_audio_capability(entry):
    """Stable API-facing live-audio capability declared by a model entry.

    Unknown or user-added models default to no advertised live support until
    their configuration explicitly declares it. This avoids inferring runtime
    behavior from a repository name.
    """
    value = entry.get("live_audio")
    if not isinstance(value, dict):
        return {"audio": "none", "response_format": None, "sample_rate": None}
    mode = value.get("mode")
    if mode not in ("native", "segmented"):
        return {"audio": "none", "response_format": None, "sample_rate": None}
    sample_rate = value.get("sample_rate")
    try:
        sample_rate = int(sample_rate) if sample_rate is not None else None
    except (TypeError, ValueError):
        sample_rate = None
    return {
        "audio": mode,
        "response_format": value.get("response_format") or "pcm",
        "sample_rate": sample_rate,
    }


def group_by_capability(entries):
    """[(capability, title, [entries])] in CAPABILITIES order, skipping empty groups."""
    groups = []
    for capability, (title, _description) in CAPABILITIES.items():
        members = [entry for entry in entries if capability_for(entry) == capability]
        if members:
            groups.append((capability, title, members))
    return groups


def engine_label(entry):
    engine = engine_for(entry)
    if engine.key == "qwen3":
        return f"{engine.label} · {QWEN_VARIANTS.get(entry.get('qwen_variant'), 'unknown variant')}"
    if engine.key in DUAL_MODE_ENGINES:
        return f"{engine.label} · {VOICE_MODES[entry_mode(entry)]}"
    return engine.label


def entry_to_json(entry):
    payload = {key: entry[key] for key in ENTRY_KEYS if key in entry}
    if payload.get("backend") != "multilingual":
        payload.pop("multilingual_t3_model", None)
    if payload.get("backend") != "qwen3":
        payload.pop("qwen_variant", None)
    payload.pop("voxcpm_mode", None)
    if payload.get("backend") in DUAL_MODE_ENGINES:
        payload["mode"] = entry_mode(entry)
    else:
        payload.pop("mode", None)
    if payload.get("multilingual_t3_model", "").endswith(".safetensors"):
        for short, filename in WEIGHT_VERSIONS.items():
            if payload["multilingual_t3_model"] == filename:
                payload["multilingual_t3_model"] = short
    if not payload.get("test_texts"):
        payload.pop("test_texts", None)
    return payload


def save_models_config(path, entries):
    payload = {"models": [entry_to_json(entry) for entry in entries]}
    temp_path = path + ".tmp"
    # LF endings to match the tracked file (no CRLF churn on Windows).
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp_path, path)


def is_valid_repo_id(repo_id):
    return bool(REPO_ID_PATTERN.match(repo_id or ""))


# ---------- local cache ----------

def cached_repo_sizes():
    """{repo_id: bytes on disk} for every model repo in the local HF cache."""
    try:
        info = scan_cache_dir()
    except Exception:
        return {}
    return {repo.repo_id: repo.size_on_disk for repo in info.repos if repo.repo_type == "model"}


def is_downloaded(entry):
    try:
        cached = try_to_load_from_cache(entry["repo_id"], key_weight_file(entry))
    except Exception:
        return False
    if not (isinstance(cached, str) and os.path.exists(cached)):
        return False
    if entry.get("backend") == "kokoro":
        return any(name.endswith(".pth") for name in os.listdir(os.path.dirname(cached)))
    if entry.get("backend") == "vibevoice":
        with open(cached, encoding="utf-8") as handle:
            shards = set(json.load(handle).get("weight_map", {}).values())
        return all(os.path.exists(os.path.join(os.path.dirname(cached), shard)) for shard in shards)
    return True


# ---------- hardware needs ----------

@dataclass
class HardwareNeeds:
    min_gb: float      # GPU memory to run at all
    good_gb: float     # GPU memory for full speed (e.g. full-size batches)
    cpu_ok: bool       # usable without an NVIDIA GPU
    note: str


def _billions(repo_id):
    """Parameter count from names like Qwen3-TTS-12Hz-1.7B or qwen3-tts-1-7b."""
    match = re.search(r"(?<![\d.])(\d+)[._-](\d)b(?![a-z])", repo_id.lower())
    return float(f"{match.group(1)}.{match.group(2)}") if match else None


def hardware_needs(backend, repo_id=""):
    """Rough GPU memory needs, measured on an RTX 5070 Ti where noted."""
    if backend == "kokoro":
        return HardwareNeeds(2, 2, True, "Small model; also quick on a CPU.")
    if backend == "vibevoice":
        if (_billions(repo_id) or 1.5) >= 5 or "7b" in (repo_id or "").lower() or "large" in (repo_id or "").lower():
            return HardwareNeeds(20, 24, False, "The 7B model needs a 24 GB GPU. Very slow on a CPU.")
        return HardwareNeeds(8, 10, False, "Peaks around 6.2 GB for a short exchange, more for long "
                                           "sections. Very slow on a CPU.")
    if backend == "omnivoice":
        return HardwareNeeds(3, 4, False, "Peaks around 2.1 GB for one section and 3.4 GB for a "
                                          "batch of 8. Not tested on a CPU.")
    if backend == "voxcpm":
        if (_billions(repo_id) or 2) < 1:
            return HardwareNeeds(4, 6, False, "The original 0.5B VoxCPM. Very slow on a CPU.")
        return HardwareNeeds(6, 8, False, "Peaks around 5.9 GB while generating. Very slow on a CPU.")
    if backend == "qwen3":
        if (_billions(repo_id) or 1.7) < 1:
            return HardwareNeeds(4, 8, False, "Smaller batches below 8 GB. Very slow on a CPU.")
        return HardwareNeeds(6, 12, False, "Long documents peak near 10 GB at full batch size; "
                                           "smaller GPUs use smaller batches. Very slow on a CPU.")
    return HardwareNeeds(4, 6, True, "Peaks around 3.2 GB while generating. Works on a CPU, "
                                     "about 8x slower.")


def format_size(num_bytes):
    if not num_bytes:
        return "0 MB"
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.1f} GB"
    return f"{num_bytes / 1024 ** 2:.0f} MB"


# ---------- Hugging Face lookups ----------

@dataclass
class RepoCheck:
    ok: bool
    message: str
    detected_backend: str = ""
    weight_versions: tuple = ()
    download_bytes: int = 0
    gated: bool = False
    private: bool = False
    qwen_variant: str = ""
    license: str = ""


def check_repo(repo_id, token=None):
    """Inspect a Hugging Face repo and work out which engine can load it."""
    if not is_valid_repo_id(repo_id):
        return RepoCheck(False, "Enter a repo in the form owner/name.")
    api = HfApi()
    try:
        info = api.model_info(repo_id, files_metadata=True, token=token or None)
    except GatedRepoError:
        return RepoCheck(False, "This repo is gated: accept its terms on huggingface.co, "
                                "then add a token under Hugging Face access.", gated=True)
    except RepositoryNotFoundError:
        return RepoCheck(False, "Repo not found. Check the name, or add a token if it is private.")
    except HfHubHTTPError as exc:
        return RepoCheck(False, f"Hugging Face returned an error: {exc}")
    except Exception as exc:
        return RepoCheck(False, f"Could not reach Hugging Face: {exc}")

    sizes = {sibling.rfilename: (sibling.size or 0) for sibling in (info.siblings or [])}
    license_id = license_from_tags(getattr(info, "tags", None)) or str(
        getattr(getattr(info, "card_data", None), "license", "") or "").lower()
    license_id = resolve_license(repo_id, getattr(info, "tags", None), license_id)
    versions = tuple(short for short, filename in WEIGHT_VERSIONS.items() if filename in sizes)
    gated = bool(getattr(info, "gated", False))
    private = bool(getattr(info, "private", False))
    if versions or "t3_23lang.safetensors" in sizes:
        backend = "multilingual"
        best = versions[0] if versions else None
        files = ENGINE_FILES["multilingual"] + ([WEIGHT_VERSIONS[best]] if best else ["t3_23lang.safetensors"])
    elif "t3_cfg.safetensors" in sizes:
        backend, files = "legacy", ENGINE_FILES["legacy"]
    elif _is_vibevoice_layout(sizes, repo_id):
        download = sum(size for name, size in sizes.items() if not name.startswith("figures/"))
        access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
        return RepoCheck(True, f"Found: VibeVoice · about {format_size(download)} to download · {access}",
                         "vibevoice", (), download, gated, private, "",
                         resolve_license(repo_id, backend="vibevoice"))
    elif _is_omnivoice_layout(sizes, getattr(info, "tags", None)):
        download = sum(sizes.values())
        access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
        return RepoCheck(True, f"Found: OmniVoice · about {format_size(download)} to download · {access}",
                         "omnivoice", (), download, gated, private, "",
                         resolve_license(repo_id, backend="omnivoice"))
    elif _is_voxcpm_layout(sizes):
        download = sum(sizes.values())
        access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
        return RepoCheck(True, f"Found: VoxCPM · about {format_size(download)} to download · {access}",
                         "voxcpm", (), download, gated, private, "", license_id)
    elif _is_kokoro_layout(sizes):
        files = [name for name in sizes if name.endswith(".pth") and "/" not in name] + ["config.json"]
        files += [name for name in sizes if name.startswith("voices/") and name.endswith(".pt")]
        voices = sum(1 for name in files if name.startswith("voices/"))
        download = sum(sizes.get(name, 0) for name in files)
        access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
        return RepoCheck(True, f"Found: Kokoro, {voices} voices · about {format_size(download)} to "
                               f"download · {access}", "kokoro", (), download, gated, private, "", license_id)
    elif "config.json" in sizes and "model.safetensors" in sizes:
        result = _check_qwen_repo(repo_id, token, sizes, gated, private)
        result.license = license_id
        return result
    else:
        weights = sorted(name for name in sizes
                         if name.endswith((".safetensors", ".pt", ".bin", ".gguf", ".onnx")))
        found = ", ".join(weights[:4]) + (" ..." if len(weights) > 4 else "") if weights else "no model files"
        return RepoCheck(False, f"Not a layout this app can load (found: {found}). Chatterbox "
                                "multilingual repos have t3_mtl23ls_v3.safetensors; English ones "
                                "have t3_cfg.safetensors; Kokoro repos have a .pth, config.json "
                                "and voices such as voices/af_heart.pt.", gated=gated, private=private)
    download = sum(sizes.get(name, 0) for name in files)
    engine = ENGINES[backend]
    access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
    detail = f"Found: {engine.label}"
    if versions:
        detail += f", weights {', '.join(v.upper() for v in versions)}"
    detail += f" · about {format_size(download)} to download · {access}"
    return RepoCheck(True, detail, backend, versions, download, gated, private, "", license_id)


# Kokoro voice names start with a language letter; these are the ones its pipeline
# can speak here (US/UK English, Spanish, French, Hindi, Italian, Portuguese, Mandarin).
KOKORO_VOICE = re.compile(r"^voices/[abefhipz][fm]_\w+\.pt$")


def _is_omnivoice_layout(files, tags=None):
    """OmniVoice repos hold the LLM weights plus a Higgs audio tokenizer folder."""
    return ("model.safetensors" in files and "audio_tokenizer/model.safetensors" in files
            and ("omnivoice" in {tag.lower() for tag in tags or []} or "chat_template.jinja" in files))


def _is_voxcpm_layout(files):
    """VoxCPM repos pair an AudioVAE with the language model weights."""
    return "audiovae.pth" in files and ("model.safetensors" in files or "pytorch_model.bin" in files)


def _is_kokoro_layout(files):
    return ("config.json" in files and any(name.endswith(".pth") and "/" not in name for name in files)
            and any(KOKORO_VOICE.match(name) for name in files))


def _is_vibevoice_layout(files, repo_id):
    """Transformers-format VibeVoice repos (the "-hf" conversions): a processor and chat
    template beside sharded weights. Microsoft's original-format repos, ASR and
    realtime models load differently and are skipped."""
    name = (repo_id or "").lower()
    return ("vibevoice" in name and not any(word in name for word in ("asr", "realtime", "streaming"))
            and "chat_template.jinja" in files and "processor_config.json" in files
            and "model.safetensors.index.json" in files)


def _check_qwen_repo(repo_id, token, sizes, gated, private):
    try:
        with open(hf_hub_download(repo_id, "config.json", token=token or None), encoding="utf-8") as handle:
            config = json.load(handle)
    except Exception as exc:
        return RepoCheck(False, f"Could not read config.json: {exc}", gated=gated, private=private)
    variant = config.get("tts_model_type", "")
    if config.get("model_type") != "qwen3_tts" or variant not in QWEN_VARIANTS:
        return RepoCheck(False, "This repo has a config.json but is not a Qwen3-TTS speech model "
                                "this app can load.", gated=gated, private=private)
    download = sum(sizes.values())
    access = "gated (token needed)" if gated else "private (token needed)" if private else "public"
    return RepoCheck(True, f"Found: Qwen3-TTS, {QWEN_VARIANTS[variant].lower()} "
                           f"· about {format_size(download)} to download · {access}",
                     "qwen3", (), download, gated, private, variant)


# ---------- discovery ----------

CHATTERBOX_KEY_FILES = {"t3_mtl23ls_v3.safetensors", "t3_mtl23ls_v2.safetensors",
                        "t3_23lang.safetensors", "t3_cfg.safetensors"}
# Same names, different formats (Apple MLX, GGUF, ONNX ...) or test fixtures.
EXCLUDED_TAGS = {"mlx", "mlx-audio", "gguf", "onnx", "openvino", "coreml", "coremltools", "ggml"}
QWEN_NAME_HINTS = {"customvoice": "custom_voice", "voicedesign": "voice_design", "base": "base"}
KNOWN_LANGUAGE_TAGS = {
    "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko", "ms", "nl",
    "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh", "id", "bn", "fa", "uk", "vi", "th", "cs",
    "ro", "hu", "ur", "ta", "te", "mos",
}


@dataclass
class SearchResult:
    repo_id: str
    backend: str
    summary: str
    downloads: int
    likes: int
    gated: bool
    languages: tuple
    updated: str
    qwen_variant: str = ""
    license: str = ""

    @property
    def capability(self):
        return capability_for({"backend": self.backend, "qwen_variant": self.qwen_variant})

    @property
    def capabilities(self):
        if self.backend in DUAL_MODE_ENGINES and "voice design" in self.summary:
            return {"clone", "design"}
        return {self.capability}


def _classify(model):
    tags = {tag.lower() for tag in (model.tags or [])}
    if tags & EXCLUDED_TAGS or "tiny-random" in model.id.lower() or "mlx" in model.id.lower():
        return None
    files = {sibling.rfilename for sibling in (model.siblings or [])}
    if files & CHATTERBOX_KEY_FILES:
        if files & (CHATTERBOX_KEY_FILES - {"t3_cfg.safetensors"}):
            versions = [v.upper() for v, f in WEIGHT_VERSIONS.items() if f in files]
            return "multilingual", "", "Chatterbox multilingual" + (f" {'/'.join(versions)}" if versions else "")
        return "legacy", "", "Chatterbox original (single language)"
    if "qwen3_tts" in tags and "model.safetensors" in files and "config.json" in files:
        name = model.id.split("/")[-1].lower().replace("-", "").replace("_", "")
        variant = next((v for hint, v in QWEN_NAME_HINTS.items() if hint in name), "")
        size = "0.6B " if "0.6b" in model.id.lower() else "1.7B " if "1.7b" in model.id.lower() else ""
        label = QWEN_VARIANTS.get(variant, "variant confirmed by Check").lower()
        return "qwen3", variant, f"Qwen3-TTS {size}· {label}"
    if _is_vibevoice_layout(files, model.id):
        size = "7B" if "7b" in model.id.lower() else "1.5B" if "1.5b" in model.id.lower() else ""
        return "vibevoice", "", f"VibeVoice {size} · conversations".replace("  ", " ")
    if _is_omnivoice_layout(files, tags):
        return "omnivoice", "", "OmniVoice · cloning and voice design"
    if _is_voxcpm_layout(files):
        version = "VoxCPM2" if "voxcpm2" in model.id.lower() else "VoxCPM"
        return "voxcpm", "", f"{version} · cloning and voice design" if version == "VoxCPM2" \
            else f"{version} · voice cloning"
    if _is_kokoro_layout(files):
        voices = sum(1 for name in files if name.startswith("voices/") and name.endswith(".pt"))
        return "kokoro", "", f"Kokoro · {voices} voices"
    return None


def search_models(query="", engine="all", token=None, limit=40):
    """Find Hugging Face repos this app can load, most downloaded first."""
    api = HfApi()
    expand = ["siblings", "downloads", "likes", "gated", "tags", "lastModified"]
    query = (query or "").strip()
    listings = []
    if engine in ("all", "chatterbox"):
        listings.append(dict(filter="chatterbox", search=query or None))
        listings.append(dict(search=query or "chatterbox"))
    if engine in ("all", "qwen3"):
        listings.append(dict(filter="qwen3_tts", search=query or None))
        if query:
            listings.append(dict(search=query))
    if engine in ("all", "vibevoice"):
        listings.append(dict(search=f"vibevoice {query}".strip() if query else "vibevoice"))
    if engine in ("all", "omnivoice"):
        listings.append(dict(search=f"omnivoice {query}".strip() if query else "omnivoice"))
    if engine in ("all", "voxcpm"):
        listings.append(dict(search=f"voxcpm {query}".strip() if query else "voxcpm"))
    if engine in ("all", "kokoro"):
        listings.append(dict(search=f"kokoro {query}".strip() if query else "kokoro"))
    seen, results = set(), []
    for kwargs in listings:
        try:
            models = api.list_models(sort="downloads", limit=200, expand=expand,
                                     token=token or None, **kwargs)
            for model in models:
                if model.id in seen:
                    continue
                seen.add(model.id)
                classified = _classify(model)
                if not classified:
                    continue
                backend, variant, summary = classified
                family = "chatterbox" if backend in ("multilingual", "legacy") else backend
                if engine != "all" and family != engine:
                    continue
                tags = {tag.lower() for tag in (model.tags or [])}
                languages = tuple(sorted(tags & KNOWN_LANGUAGE_TAGS))
                updated = model.last_modified.strftime("%b %Y") if getattr(model, "last_modified", None) else ""
                results.append(SearchResult(model.id, backend, summary, model.downloads or 0,
                                            model.likes or 0, bool(model.gated), languages, updated, variant,
                                            resolve_license(model.id, model.tags,
                                                            license_from_tags(model.tags), backend)))
        except Exception as exc:
            raise RuntimeError(f"Hugging Face search failed: {exc}") from exc
    results.sort(key=lambda result: result.downloads, reverse=True)
    return results[:limit]


def whoami(token):
    try:
        info = HfApi().whoami(token=token)
    except Exception as exc:
        return False, f"Token not accepted: {exc}".split("\n")[0][:160]
    return True, f"Signed in as {info.get('name', 'unknown')}"
