# VAVE - Personal AI Control Plane
## Project Documentation & Architecture Summary

### Overview
VAVE is a Personal AI Control Plane that translates human intent into coordinated, multi-agent execution across machines, services, and data sources. Unlike a simple chatbot, VAVE acts as the central hub for local UI automation, web scraping, API interactions, and cross-device synchronization (PC, mobile, and cloud).

### Core Features
1. **Local-First AI Brain**: Powered by local LLMs (via Ollama).
2. **Zero-Trust Safety Guard**: All agent tool calls are intercepted by guard.py. Destructive actions or sensitive data exfiltration require explicit human approval via the GUI or Telegram.
3. **Cross-Platform Secret Management**: Credentials (like Telegram tokens and Gmail App Passwords) are encrypted at rest in a local SQLite database (control.db) and are never exposed in plain text in config files.
4. **Omni-Channel Control**: Command VAVE via voice wake-words, a modern dark-mode CustomTkinter Desktop GUI, or remotely via a Telegram bot.
5. **Web & Desktop Separation**: VAVE intelligently distinguishes between local apps (using PyAutoGUI/UIAutomation) and web tasks (using Playwright), isolating the tools available to the AI depending on the context.

### Recent System Upgrades
* **Concurrency & Process Safety**: Implemented strict PID-based cross-process locking for daemon threads (e.g., Telegram sync) to prevent 409 Conflict errors when multiprocessing libraries spawn worker pools.
* **Encrypted Credential Store**: Migrated email credentials from plain text config.json into the SecretStore, automatically decrypting them on the fly for IMAP/SMTP operations.
* **Web Security Hardening**: Web browsing (rowse, rowser_fill_form) is now strictly classified under the Zero-Trust policy. When commanded remotely (e.g., Telegram), VAVE will demand user confirmation before interacting with arbitrary websites.
* **Cross-Platform Stability**: Hardened the CustomTkinter GUI against OS-specific datetime formatting bugs (%-d vs %#d).

### Setup & Usage
Refer to the README.md for full installation instructions. 
* To run the GUI: python vave_gui.py
* To run headless/voice mode: python main.py
* To run in safe smoke-test mode: python main.py --smoke-test

### Security Notice
VAVE is designed with safety first. If the AI attempts to open a sensitive URL or execute a system command, you will see a Confirmation Request in the console or via Telegram. If no response is given within 60 seconds, the action is automatically blocked.
