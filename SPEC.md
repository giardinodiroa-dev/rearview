# ShadowTasker — Full Technical Specification

> Version 0.1.0 — Open Source Template Architecture
> License: MIT

---

## Overview

ShadowTasker is a programmable browser automation assistant for sales agents (and anyone doing repetitive browser workflows). It runs a target web app silently in a hidden virtual desktop (Xvfb), attaches to the browser via CDP, watches for workflow state changes, and surfaces a minimal always-on-top toast on the user's real screen.

The first official template is **Aloware** — a cloud power dialer. The system is designed so the community can add templates for any web app (HubSpot, Salesforce, JustCall, PhoneBurner, Outreach, etc.).

---

## Goals

1. Zero context-switch tax — user stays on their real desktop at all times
2. Voice-driven teleprompter — script scrolls with the user's speech in real time
3. One-keypress workflow advancement — disposition + next call from the toast
4. Fully configurable, open-source, plugin-template architecture
5. Works headlessly — no GUI required to run, toast is the only visible surface

---

## Architecture

```
Real Display (:0)                    Virtual Display (:99 Xvfb)
┌──────────────────────────┐         ┌──────────────────────────┐
│   User's actual work     │         │   Chromium (hidden)      │
│                          │   CDP   │   Aloware dialer tab     │
│  ┌────────────────────┐  │◄───────►│                          │
│  │  ShadowToast (Qt6) │  │ events  │  MutationObserver        │
│  │  bottom-right      │  │         │  Network interceptor     │
│  │  retractable       │  │         │                          │
│  └────────────────────┘  │         └──────────────────────────┘
│                          │
│  pynput global hotkeys   │
└──────────────────────────┘
```

### Core Components

| Module | Responsibility |
|---|---|
| `cli.py` | Typer CLI: `start`, `stop`, `status`, `config`, `script` |
| `desktop.py` | Xvfb lifecycle, Chromium launch, display management |
| `watcher.py` | CDP attach, call-end event detection (DOM + network) |
| `scraper.py` | Contact card DOM scraper → structured contact dict |
| `disposition.py` | JS-inject disposition selection + submit in Aloware |
| `toast/window.py` | PyQt6 always-on-top retractable toast |
| `toast/teleprompter.py` | Script display widget + voice-position tracking |
| `toast/components.py` | Reusable Qt widgets (DispositionButton, ContactCard) |
| `speech/listener.py` | Real-time mic capture → text via faster-whisper |
| `hotkeys.py` | Global pynput hotkey bindings |
| `config.py` | YAML config loader + validator |
| `templates/aloware.py` | Aloware-specific selectors + workflow logic |

---

## Toast UI Specification

### Layout (collapsed = pill, expanded = card)

```
EXPANDED (default on call end):
┌──────────────────────────────────────────────┐  ← bottom-right
│  ▼ [hide]                          [⚙] [✕]  │
├──────────────────────────────────────────────┤
│  TELEPROMPTER                                │
│  ┌──────────────────────────────────────────┐│
│  │ "Hi John, I'm calling from... my name is ││  ← scrolls with voice
│  │  ▶ [James] and I wanted to reach out..." ││  ← current word highlighted
│  └──────────────────────────────────────────┘│
├──────────────────────────────────────────────┤
│  CONTACT                                     │
│  John Smith · Acme Corp                      │
│  "B2B sales automation tools"                │
│  📞 +1 555-123-4567                          │
├──────────────────────────────────────────────┤
│  DISPOSITION                                 │
│  [V] Voicemail  [N] No Answer  [C] Connected │
│  [F] Follow Up  [D] DNC  [W] Wrong #         │
│                         [▶ Next Call]        │
└──────────────────────────────────────────────┘

COLLAPSED (retracted pill):
                              [📞 ShadowTasker ▲]
```

### Toast Behavior

- **Position**: Bottom-right, 20px margin from screen edge
- **Always-on-top**: Qt WindowStaysOnTopHint + X11 EWMH `_NET_WM_STATE_ABOVE`
- **Retract**: Click [hide] collapses to pill; click pill to expand
- **Position memory**: Saves last X/Y to config between sessions
- **Animation**: Slide-up on appear, slide-down on retract (150ms ease)
- **Auto-show**: Appears automatically on call-end event
- **Auto-hide**: Optional — hides N seconds after disposition selected (configurable)
- **Opacity**: Configurable (default 0.92)

---

## Teleprompter Specification

### Behavior

1. User loads a script file (Markdown or plain text) via `shadowtasker script load scripts/my_script.md`
2. Speech listener runs continuously while toast is active
3. Real-time STT (faster-whisper small model, runs locally offline) transcribes mic audio
4. Transcribed words are matched against the script using fuzzy sliding-window match
5. Script display auto-scrolls to current position, highlights active phrase
6. If user goes off-script (silence > 5s or no match), scroll pauses and waits

### Script Format

```markdown
# Opening
Hi {contact_name}, this is {agent_name} calling from {company}.

# Pitch
I wanted to quickly reach out because...

# Objection: Not interested
I completely understand. Many of our clients felt the same way before...

# Close
Does {next_tuesday} work for a 15-minute call?
```

Variables `{contact_name}` etc. are injected from the contact card data at call start.

### Future: AI Suggestions (v0.2)

When the caller says something that matches an objection pattern, a suggestion card slides in below the script:

```
💡 Suggested response: "That's a fair point. Most teams we work with..."
```

This is a placeholder — the architecture supports it but it is not in v0.1.

---

## Workflow Engine (Template API)

Templates define the workflow for a specific web app. Each template implements:

```python
class BaseTemplate:
    # Selectors — override per app
    CALL_ACTIVE_SELECTOR: str       # DOM element present during call
    CALL_ENDED_SELECTOR: str        # DOM element present after call ends
    CONTACT_NAME_SELECTOR: str
    CONTACT_COMPANY_SELECTOR: str
    CONTACT_DESCRIPTION_SELECTOR: str
    CONTACT_PHONE_SELECTOR: str
    DISPOSITION_CONTAINER_SELECTOR: str
    NEXT_CALL_BUTTON_SELECTOR: str

    async def on_call_start(self, page, contact: dict): ...
    async def on_call_end(self, page, contact: dict): ...
    async def set_disposition(self, page, label: str): ...
    async def advance_to_next(self, page): ...
    async def scrape_contact(self, page) -> dict: ...
```

The Aloware template ships with v0.1. Community can add templates by subclassing `BaseTemplate` and registering in `templates/`.

---

## Configuration

### `config/settings.yaml`

```yaml
shadowtasker:
  display: ":99"              # virtual display
  real_display: ":0"          # your actual screen
  template: "aloware"         # active template

browser:
  executable: "chromium"      # or "google-chrome", "brave"
  debug_port: 9222            # CDP remote debug port
  connect_existing: true      # attach to running browser vs launch new

workflow:
  auto_advance: false         # auto-dial next after disposition
  auto_hide_after: 0          # hide toast N seconds after disposition (0 = never)
  hotkey_next: "<super>s"     # manual trigger hotkey

toast:
  position: "bottom-right"
  opacity: 0.92
  width: 420
  theme: "dark"               # dark | light | auto

teleprompter:
  enabled: true
  script: "scripts/default.md"
  stt_model: "small"          # faster-whisper model size
  agent_name: "James"
  company: "MyCompany"

agent:
  name: "James"
```

### `config/dispositions.yaml`

```yaml
dispositions:
  - label: "Voicemail"
    hotkey: "v"
  - label: "No Answer"
    hotkey: "n"
  - label: "Connected"
    hotkey: "c"
  - label: "Follow Up"
    hotkey: "f"
  - label: "DNC"
    hotkey: "d"
  - label: "Wrong Number"
    hotkey: "w"
```

---

## CLI Reference

```bash
shadowtasker start              # launch virtual desktop + browser, attach CDP
shadowtasker stop               # teardown everything
shadowtasker status             # show current call state, config, connection
shadowtasker config             # open settings.yaml in $EDITOR
shadowtasker script load <path> # set active teleprompter script
shadowtasker script edit        # open current script in $EDITOR
shadowtasker dispositions       # list/edit dispositions.yaml
shadowtasker connect            # (re)attach CDP to running browser tab
shadowtasker vnc                # start x11vnc for visual inspection of :99
```

---

## Dependencies

```
python >= 3.11

# Browser automation
playwright          # CDP + browser control
playwright-stealth  # optional anti-detection

# UI
PyQt6               # toast window

# Speech
faster-whisper      # offline real-time STT
sounddevice         # microphone capture
numpy               # audio buffer

# Hotkeys
pynput              # global hotkey listener

# Virtual display
xvfbwrapper         # Xvfb Python bindings

# Config + CLI
typer               # CLI framework
pyyaml              # config parsing
rich                # terminal output

# Utilities
thefuzz             # fuzzy script matching for teleprompter
python-dotenv       # .env support
```

System packages: `xvfb`, `x11vnc`, `openbox`, `chromium`

---

## Data Flow — Call Lifecycle

```
1. shadowtasker start
   └─ Xvfb :99 started
   └─ Chromium launched on :99 with --remote-debugging-port=9222
   └─ CDP connected via Playwright
   └─ Aloware template injected (MutationObserver + network hooks)
   └─ Toast shown on :0 in idle state
   └─ Speech listener starts (mic hot)

2. Call begins (user dials or power dialer auto-dials)
   └─ on_call_start fires
   └─ Contact scraped from DOM
   └─ Script variables injected ({contact_name} etc.)
   └─ Toast shows contact + teleprompter
   └─ Teleprompter script scrolls to top, ready

3. Conversation
   └─ Microphone → faster-whisper → transcript chunks
   └─ Fuzzy match against script → scroll position
   └─ Toast teleprompter updates in real time

4. Call ends (DOM change detected OR Super+S pressed)
   └─ on_call_end fires
   └─ Toast flashes disposition section
   └─ User presses hotkey (v/n/c/f/d/w) or clicks button
   └─ set_disposition() JS-injects click into Aloware
   └─ If auto_advance: true → advance_to_next() fires
   └─ Toast resets to idle / next contact

5. shadowtasker stop
   └─ CDP disconnects
   └─ Chromium closes
   └─ Xvfb stops
   └─ Toast closes
```

---

## Project Structure

```
shadowtasker/
├── SPEC.md
├── README.md
├── pyproject.toml
├── shadowtasker/
│   ├── __init__.py
│   ├── cli.py
│   ├── desktop.py
│   ├── watcher.py
│   ├── scraper.py
│   ├── disposition.py
│   ├── hotkeys.py
│   ├── config.py
│   ├── toast/
│   │   ├── __init__.py
│   │   ├── window.py
│   │   ├── teleprompter.py
│   │   └── components.py
│   ├── speech/
│   │   ├── __init__.py
│   │   └── listener.py
│   └── templates/
│       ├── __init__.py
│       ├── base.py
│       └── aloware.py
├── config/
│   ├── settings.yaml
│   └── dispositions.yaml
├── scripts/
│   └── default.md
└── templates/
    └── aloware/
        └── README.md
```

---

## Open Source Template Contribution Guide

To add a new template (e.g., HubSpot):

1. Create `shadowtasker/templates/hubspot.py`
2. Subclass `BaseTemplate`
3. Override selectors and any workflow methods that differ
4. Add `"hubspot"` to `templates/__init__.py` registry
5. Create `templates/hubspot/README.md` with setup instructions
6. Submit PR

The template API is the only surface area — no core changes needed.

---

## Roadmap

| Version | Feature |
|---|---|
| v0.1 | Core system: Xvfb, CDP, Aloware template, toast, dispositions, teleprompter |
| v0.2 | AI objection suggestions in teleprompter |
| v0.3 | Call recording + post-call summary |
| v0.4 | CRM sync (push disposition + notes to HubSpot/Salesforce) |
| v0.5 | Multi-template support UI, community template browser |
