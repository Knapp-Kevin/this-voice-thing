# Upstream Provenance and Legacy Cleanup Audit

Status: **Initial evidence inventory; not a legal clearance or dead-code certification.**

Audited: 2026-10-09. Scope: the `main` branch file trees of `Knapp-Kevin/this-voice-thing` and `AcTePuKc/Chatterbox-TTS-UI`. Upstream commit tree: `22460fd99ceb78c309f879b93878240241a0dc9b`. This Voice Thing baseline tree: `17657c653d9bed89f86b526cbae704cf12308d36`.

## Verified findings

- Upstream contains **17 tracked files**, including **4 Python files**. This Voice Thing contains **121 tracked files**, including **78 Python files**. Counts describe architectural expansion, **not** independently authored code share.
- Git object hashes demonstrate these **exact, byte-identical** upstream payloads are still distributed:

| Upstream file | Current file | Disposition |
| --- | --- | --- |
| `model_backends.py` | `this_voice_thing/engines/chatterbox_backend.py` | **Retained upstream implementation under a different path. Keep attribution.** |
| `requirements.in` | `requirements.in` | **Identical upstream dependency declaration.** |
| `requirements.lock.txt` | `requirements.lock.txt` | **Identical upstream dependency lock.** |
| `LICENSE` | `LICENSE` | **Identical MIT notice. Preserve.** |
| `screenshot_1.png` | `docs/screenshots/upstream_original.png` | **Identical upstream screenshot.** Historical-only; removable if unreferenced, but do not remove attribution for other surviving upstream code. |

- `main.py` shrank from a 76 KB upstream application to a 221-byte entry point; that does **not** prove upstream UI implementation was rewritten. `this_voice_thing/ui/common.py` retains the upstream-style device-selection/bootstrap code near its top; function-level comparison remains open.
- Setup scripts, launchers, and the product README have changed Git hashes, but changed hashes alone do **not** prove independent authorship.
- GitHub reports this repository as an actual fork of `AcTePuKc/Chatterbox-TTS-UI`. Repo metadata may be detached as a separate hosting operation, but that does not extinguish third-party copyrights or the MIT notice requirements.

## Rules for cleanup

1. **Keep MIT and upstream copyright/permission notices** in distributed copies while upstream-origin code or substantial portions remain. New original work may carry its own authorship notice after ownership confirmation.
2. Preserve the **Chatterbox engine identity**, Resemble AI references, and the official model dependency. They describe currently supported functionality, not obsolete UI branding.
3. Keep `chatterbox_outputs/` as a compatibility path until a migration handles existing files, saved configuration and external scripts without data loss.
4. No bulk code deletion or automatic renaming based on the string “chatterbox.” Require a caller/reference scan, tests and migration plan.
5. Distinguish license for the **application source** from each engine's **model weights and their separate terms**.

## Next provenance verification

- Compare upstream `main.py` functions and UI sections against current `ui/common.py`, `ui/main_window.py`, `ui/pages/*`, `ui/threads.py` and `app.py`, including moved/modified blocks.
- Compare upstream `install_torch.py` with `scripts/install_torch.py`; compare both `setup_env` scripts and `run` launchers line by line.
- Identify all code copied or materially adapted from upstream, even where filenames/structures differ. Record file-to-file/function-to-function relationships and licenses.
- Check whether `docs/screenshots/upstream_original.png` is linked in documentation. Only delete it if it is unused and removal serves the project.
- Audit runtime data paths and legacy symbols before any renaming. Add migration/compatibility tests before changing paths.
- Run application unit tests after any cleanup and review the complete diff for retained notices.

## Release decisions

**Current disposition:** independent product branding is appropriate; **upstream attribution remains required** on present evidence. Do not characterize this repository as a clean-room rewrite, remove the original MIT notice, or claim a verified replacement percentage.

**Out of scope for this documentation-only audit:** runtime refactoring, model downloads, hardware tests, GitHub fork detachment, relicensing, and deletion of legacy user data.
