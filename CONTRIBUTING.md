# Contributing to This Voice Thing

First off, thank you for considering contributing to This Voice Thing! We appreciate any help, whether it's reporting a bug, suggesting a feature, or submitting code changes.

This document provides some guidelines to help make the contribution process smooth and effective for everyone.

## How Can I Contribute?

There are several ways you can contribute:

*   **Reporting Bugs:** If you find a bug, please open an issue on GitHub. Try to include as much information as possible:
    *   A clear and descriptive title.
    *   Steps to reproduce the bug.
    *   What you expected to happen.
    *   What actually happened (including any error messages or console output).
    *   Your operating system, Python version, and versions of key libraries (especially PyTorch, PySide6, and the affected speech engine).
    *   Screenshots can also be very helpful!

*   **Suggesting Enhancements or New Features:** If you have an idea for a new feature or an improvement to an existing one, please open an issue on GitHub. Describe your idea clearly and why you think it would be a valuable addition.

*   **Code Contributions (Pull Requests):** We welcome pull requests for bug fixes, new features, or improvements. Please follow these guidelines:
    *   **Open an Issue First:** For anything more than a trivial fix (like a typo), please open an issue first to discuss your proposed changes. This helps ensure your work aligns with the project's goals and avoids duplicated effort.
    *   **Fork the Repository:** Fork the main repository to your own GitHub account.
    *   **Create a New Branch:** Create a new branch in your fork for your changes (e.g., `git checkout -b feature/my-new-feature` or `git checkout -b fix/bug-name`).
    *   **Code Style:** Try to follow the existing code style. We aim for readable and maintainable Python code.
    *   **Commit Messages:** Write clear and concise commit messages that explain the "what" and "why" of your changes.
    *   **Testing:** Please test your changes thoroughly on your own system before submitting a pull request. If you're adding a new feature, consider how it might be tested.
    *   **Documentation:** If your changes affect user-facing features or how the application is run, please update the `README.md` or other relevant documentation.
    *   **Pull Request:** Open a pull request from your branch to the `main` branch of the original repository. Provide a clear description of your changes in the pull request.

## Development Setup

If you're looking to contribute code, here's a quick guide to setting up your development environment:

1.  **Prerequisites:** Ensure you have Python (3.11 recommended), `uv`, and FFmpeg installed as described in the main `README.md`.
2.  **Fork & Clone:** Fork [Knapp-Kevin/this-voice-thing](https://github.com/Knapp-Kevin/this-voice-thing) on GitHub, then clone your fork (replace `YOUR-NAME`):
    ```bash
    git clone https://github.com/YOUR-NAME/this-voice-thing.git
    cd this-voice-thing
    ```
3.  **Set up the Upstream Remote (optional but recommended):**
    ```bash
    git remote add upstream https://github.com/Knapp-Kevin/this-voice-thing.git
    ```
    This lets you pull changes from the main repository. (The repository used to be called
    `Knapp-Kevin/Chatterbox-TTS-UI`; GitHub redirects the old URLs.) This Voice Thing began as a
    fork of [AcTePuKc/Chatterbox-TTS-UI](https://github.com/AcTePuKc/Chatterbox-TTS-UI); fixes that
    belong in that original UI are best offered there too.
4.  **Create Virtual Environment & Install Dependencies:**
    It's highly recommended to use the `run.bat` (Windows) or `run.sh` (macOS/Linux) script for the initial setup as it handles `uv venv`, `uv pip sync requirements.lock.txt`, and `python scripts/install_torch.py`.
    Alternatively, follow the manual installation steps in the `README.md`.
5.  **Make Your Changes:** Create a new branch and start coding!
6.  **Testing:** Run the application (`python main.py`) to test your changes, and run the tests:
    `.venv\Scripts\python.exe -m unittest discover tests`. The theme tests check contrast, colour
    meaning (amber = warning, red = error, green = success) and that widgets use theme tokens
    rather than hard-coded colours.
7.  **Where things live:** the app is the `this_voice_thing` package. The window, pages, dialogs and generation thread are in `ui/main_window.py`, the theme in `ui/theme.py`; engines in `engines/`; the model list, Hugging Face logic, documents, audio, subtitles, pronunciation and voice library in `core/`; the local API and Google Docs in `integrations/`. App data (settings, outputs, recordings) stays in the project folder; see `this_voice_thing/paths.py`. The README's Project Structure section shows the whole tree.
8.  **Qwen engine:** Qwen3-TTS runs in its own environment, `engines/qwen/.venv`, through `engines/qwen/qwen_worker.py`. Never install `qwen-tts` into the main `.venv`: it needs `transformers` 4.57.x, while Chatterbox pins 5.2.0. To work on the worker, run Python from `engines/qwen/.venv`.

## Code of Conduct

While we don't have a formal Code of Conduct document yet, please be respectful and considerate in all your interactions within the project community (issues, pull requests, discussions). Let's keep it a friendly and welcoming environment for everyone.

## Questions?

If you have questions about contributing or need clarification, feel free to open an issue and tag it as a "question".

---

Thank you for your interest in improving This Voice Thing!