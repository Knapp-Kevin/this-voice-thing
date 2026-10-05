# Changelog

All notable changes to **This Voice Thing**, which began as a fork of [AcTePuKc/Chatterbox-TTS-UI](https://github.com/AcTePuKc/Chatterbox-TTS-UI), are listed here.

## [Unreleased] - 2026-10-02

Changes since upstream commit `22460fd` ("Add best-effort macOS/Linux shell launcher flow").

### Added
- **`scripts/restart_app.py`** (Windows) closes the app the way its close button does, so it saves its settings, then starts it again; it forces the app only if it hangs for 30 seconds. `--close` just closes it.
- **Voice Studio.** A new **Studio** page to make voices in one place: **Clone** a recording or file (use it as is, or let a cloning model re-read a passage in that voice), **Design** from a description with several candidates at once (Qwen3, VoxCPM2, OmniVoice), or **Remix** a voice with words (VoxCPM2's styled cloning). Try any take on a test line, trim, clean up, shift pitch and speed, then **Save voice**: it's frozen as a clip and transcript, so it sounds the same in every render and batch.
- **Voice-first Generate.** Pick a voice with **Change** (yours, or a model's built-in voices); the model list shows only the models that can speak it, and picking a clip voice loads a cloning model if needed. The Voice page is now **Voices**, a library; designing moved from Generate to the Studio (which replaces Keep this voice).
- **Get a token.** The Model page's Hugging Face card links straight to Hugging Face's new-token page, with the Read type preselected.
- **Transcription (speech to text).** A new **Transcribe** page turns audio into text locally with OpenAI's Whisper large-v3 turbo (MIT license, a one-time 1.6 GB download the app asks about first). Detects the language or takes one you pick, shows optional timestamps, saves text or SRT/WebVTT subtitles, and **Send to Generate** speaks the result with any voice. **Transcribe clip** in a library voice's menu fills in its transcript for cloning models, and the local API adds an OpenAI-compatible `POST /v1/audio/transcriptions`.
- **App icon in the Windows taskbar.** The app now shows its own icon there instead of Python's, and `assets/branding/this-voice-thing.ico` is available for shortcuts.
- **Qwen3-TTS engine (optional).** Three new models alongside Chatterbox:
  - **Preset voices**, with nine speakers and plain-language style instructions.
  - **Voice design**, which creates a voice from a written description.
  - **Voice cloning**, which can use a transcript of the clip for closer likeness.

  Qwen runs in its own environment, which the app offers to install. Its output can carry the same AI watermark as Chatterbox.
- **VibeVoice engine and Conversations (optional, research use).** A new **Conversations** tab. Write a script with up to 4 speakers (`Name: line`), pick a voice for each with **Cast…** (7 sample voices, your recordings or any clip), and VibeVoice performs it as one natural conversation. Long scripts split only between turns, and each speaker keeps their voice throughout. MIT-licensed but limited to research use by its model card, so it's badged amber **Research use**.
- **OmniVoice engine (optional, non-commercial).** Fast voice cloning and voice design in 600+ languages from a 3 GB model (CC BY-NC 4.0 weights, badged red). Cloning needs the clip transcript; voice design uses an **Attributes…** picker (gender, age, pitch, whisper, accent), with unsupported words caught before generating. Sections are batched, about 3 s for four paragraphs on an RTX 5070 Ti, and a designed voice stays the same through a document.
- **License badges look past wrong tags.** Fine-tunes of a non-commercial model inherit its license even when their own tag claims a permissive one.
- **VoxCPM2 engine (optional).** One 5 GB model for voice cloning (with an optional style, and a clip transcript for closer likeness) and voice design, in 30 languages at 48 kHz. The two uses share one download and switch instantly. A designed voice stays the same through a whole document. VoxCPM runs in its own environment, which the app offers to install, and can add the same AI watermark.
- **Kokoro engine (optional).** 49 built-in voices in 7 languages from a 340 MB model, about 20× faster than Chatterbox. Pick a language, then a voice; the choice is remembered per language. Kokoro runs in its own environment, which the app offers to install, and can add the same AI watermark.
- **GPU requirements on every model tile.** Each tile shows the GPU memory the model needs next to your GPU's, in green when it fits, amber when it runs but slower, and red when the GPU is too small.
- **Faster Qwen documents.** Long text is generated in batches (up to 16 sections or about 5,000 characters per call), roughly 5× faster. Oversized batches are split automatically if the GPU runs out of memory.
- **In-app voice recording.** A guided window with countdown, live level meter, too-quiet and clipping warnings, a 30-second limit, and phonetically rich read-aloud passages. Recordings save the passage as a transcript for Qwen cloning.
- **Document narration:**
  - Open `.txt`, `.md` or `.docx` files.
  - **Preview** a sample, and **Keep this take** so the full render matches.
  - A live estimate of the time and number of sections.
  - Progress with time remaining, and **Stop** keeps finished sections as a `_partial` file.
- **Per-model time estimates.** Every model's estimated time for the current text, learned from your own runs. The estimate is a menu: pick a model from it to switch.
- **Model management in the app:**
  - Add, edit, duplicate, hide and remove models.
  - **Check** a Hugging Face repo before downloading.
  - **Find models** searches Hugging Face for repos this app can load.
  - **Model page tabs and tiles.** Tabs for voice cloning, preset voices and voice design. Your models show as tiles with download status, typical speed and a **license badge** (permissive, non-commercial or unclear). Click a tile to load it.
  - **Discover on Hugging Face** lists more loadable models for each tab, searched in the background. Click one to add it.
- **Hugging Face access** section with token **Save** and **Test**. The token is stored locally, never in `models.json`.
- **Finishing touches:** paragraph pauses, even out volume, trim silence, and WAV/FLAC output.
- **Google Docs import** (**Open... → From Google Docs...**). Paste a link to a doc shared with "anyone with the link", or sign in with Google (read-only) to search and open your own docs. Uses your own free Google Cloud OAuth client; setup steps are in the README.
- **Keep this voice.** After previewing with a voice design model (Qwen, VoxCPM, OmniVoice), keep the voice you heard: it's saved to the voice library and locked in for the full render and later renders.
- **Preview length.** Choose about 3, 5 or 10 seconds next to Preview (it was a whole section, often 15–20 s).
- **Local API** (Advanced page, off by default). Other programs on this PC can generate speech over HTTP on `127.0.0.1`: an OpenAI-compatible `POST /v1/audio/speech` (for Open WebUI, SillyTavern and the `openai` package) and a native `POST /v1/speech` with any model, library voice and subtitles. Requests use the full pipeline, take turns with the app, load models on demand, and can require a token.
- **Pronunciation dictionary.** Respell names, acronyms and jargon (`Nguyen → Win`, `SQL → sequel`) for every model, with whole-word and match-case options. **Hear it** and **Try** let you test respellings, and word lists can be imported or exported. Subtitles keep the original spelling; the Generate page shows how many words were respelled.
- **Subtitles.** Tick **Save subtitles** to get an `.srt` or `.vtt` file next to the audio. Captions are timed from the generated sections and snapped to the pauses in the speech, with no speech recognition needed. Speed changes and trimmed silence are accounted for, and conversation captions name their speaker.
- **Advanced page:** speed and pitch changes (formant-preserving) and MP3 export, with a note that they can weaken the AI watermark.
- **Voice library** (the Voice page). Save clip voices, preset voices (Kokoro, Qwen speakers) and designed voices (Qwen, VoxCPM, OmniVoice) by name, with tags and notes, and use any of them with a click. A preset or designed voice can **make a clip**: 15 s reading a phonetic passage with its exact transcript, so cloning models can reuse it. Existing recordings join the library without being moved, and VibeVoice's **Cast…** lists library voices by name.

### Changed
- **The window code is split up.** `ui/main_window.py` went from about 5,900 lines to about 400. Each page now lives in `ui/pages/` (generate, generation, documents, estimates, finishing, engine controls, player, voice, voice library, using voices, recording, transcribe, models, discover, model loading, model settings, advanced, local API, pronunciations; the Generate, Voice, Model and Advanced pages are built card by card), the dialogs in `ui/dialogs/`, and the threads, API bridge, shared setup and small widgets in their own modules. The code moved without changes, and a test checks that no two pages define the same method.
- **Organised code.** The 20-odd modules that sat in the project folder now live in a `this_voice_thing` package (`ui/`, `engines/`, `core/`, `integrations/`), with `install_torch.py` in `scripts/` and automated tests in `tests/`. `python main.py`, `run.bat` and `run.sh` work as before, and app data (settings, model list, outputs, recordings, voice library, engine environments) stays where it was.
- **New look for This Voice Thing.** A cyan / electric-blue / purple / pink identity on deep navy (dark) or light neutral surfaces (light), built on semantic theme tokens. Cyan marks actions, selection and focus; the brand gradient appears only on progress bars, the recording waveform and a thin sidebar stroke. Amber is now reserved for warnings and caution (uncertain licenses, tight hardware, setup needed), red for errors, green for success. Keyboard focus is visible everywhere, and text meets WCAG AA contrast in both themes.
- **Renamed to This Voice Thing.** The app had outgrown "Chatterbox UI"; Chatterbox remains one of its engines. Runtime names are unchanged for compatibility: outputs still go to `chatterbox_outputs/` as `chatterbox_….wav` (renaming those is a planned migration).
- **The shipped model list only holds models you can actually download.** The placeholder "Example custom…" entries and the "Legacy English compatibility" entry are gone; add an original-layout model with **+ Add repo…** if you need one. Each shipped model's download size is shown on its tile before you load it.
- **New interface:**
  - Sidebar pages (Generate, Voice, Model, Advanced, Log).
  - A light/dark theme that follows Windows, with depth and texture.
  - Plain-language controls: Expressiveness, Pacing, Variation, Take number.
- **Models are grouped by what they do:** cloning, presets, design and conversations, as tabs on the Model page.
- **Text is split where a reader would pause:**
  - Never across paragraphs or headings.
  - Overlong sentences split at clause breaks, not mid-phrase.
  - Qwen sections can hold whole paragraphs.
  - Pauses depend on the type of join.
- **The window sizes itself to its content,** so nothing needs scrolling.
- **Generated audio is saved as standard 16-bit WAV** (previously 32-bit float), with optional volume levelling and silence trimming.
- **Model settings** (repetition, min-p, top-p) are now remembered between sessions.

### Fixed
- **The window no longer widens with several finishing touches on.** The Finishing touches summary (e.g. speed, pitch, pauses, MP3) now shortens to fit, with the full text in its tooltip.
- **Qwen cloned voices no longer "reset" at section seams.** A cloned or saved designed voice drifted from its reference over long sections and snapped back at the next one. Qwen cloning now uses 300-character sections (was 600) and, while Variation is on its default, a steadier 0.5; measured on a designed voice, the drift is gone and the seams match more closely.
- **Long renders can't be cut short by a stray key.** Generate turns into Stop while rendering and kept keyboard focus, so Space or Enter could stop a render and drop its last sections. It no longer takes focus, a stop is logged again (a console redirect used to hide it), and the status says which sections weren't generated.
- **Settings are saved when a generation starts**, not only when the app closes, so a crash or forced close doesn't lose them.
- **Taskbar icon, for real this time.** Windows sometimes showed a generic icon because it asked the window for its icon while start-up work kept the window busy, then kept the generic one. The window now hands Windows its ID, icon file and relaunch command up front, so the brand icon always shows, and pinning the app pins This Voice Thing.
- **Designed voices stay frozen.** **Keep this voice** now also appears after a full render, saves the description that made the voice even if you edited the box afterwards, and **Save voice** on the Voice page saves the exact voice you heard instead of only its description (which designs a new voice each time). Saving a kept voice again edits it instead of adding a duplicate.
- **Qwen voice design no longer changes voice between sections.** The first section is designed and the rest are cloned from it with Qwen3's cloning model (voice similarity between sections 0.76 → 0.91).
- **Long voice names no longer widen the window**; the voice chip shortens them.
- **Kokoro's voice picker no longer widens the window** (its long voice names set the minimum width).
- **Windows installer:**
  - Batch files are now checked out with Windows line endings. Before, steps could run out of order and report success after a failure.
  - Newer NVIDIA drivers no longer cause a CPU-only PyTorch install.
  - A dependency URL conflict that newer `uv` versions reject is resolved.
- **Qwen works offline** once its models are downloaded.
- **A slider rounding error:** Expressiveness could go below its minimum (0.20 instead of 0.25).
- **Windows-1252 text files** now open correctly.
- **Cancelling the file browser** no longer clears the selected voice.
