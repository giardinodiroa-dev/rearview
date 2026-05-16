# ShadowTasker

Programmable browser automation assistant for sales workflows. Runs your dialer in a hidden virtual desktop, detects call end, and surfaces an always-on-top toast on your real screen — with teleprompter, contact info, and one-key dispositions.

**Current template: Aloware** — open source, add your own.

---

## Install

```bash
# System deps (Arch/Debian)
sudo pacman -S xorg-server-xvfb x11vnc openbox chromium
# or: sudo apt install xvfb x11vnc openbox chromium-browser

# Python env
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e .
playwright install chromium
```

---

## Quick Start

```bash
# 1. Edit your config
shadowtasker config

# 2. Edit your dispositions  
shadowtasker config --dispositions

# 3. Load your call script
shadowtasker script load /path/to/my_script.md

# 4. Start
shadowtasker start
```

Aloware opens in a hidden browser. The toast appears bottom-right on your real screen.

---

## Usage

```
shadowtasker start              launch everything
shadowtasker start --no-speech  skip speech recognition
shadowtasker stop               graceful shutdown
shadowtasker status             component health check
shadowtasker config             edit settings.yaml
shadowtasker config --dispositions  edit dispositions.yaml
shadowtasker script load <path> set teleprompter script
shadowtasker script edit        edit current script
shadowtasker script show        preview script in terminal
shadowtasker connect            (re)attach CDP to browser
shadowtasker vnc                start VNC for visual inspection
```

---

## Hotkeys

| Key | Action |
|---|---|
| Super+S | Manual trigger: advance to next call |
| V | Disposition: Voicemail |
| N | Disposition: No Answer |
| C | Disposition: Connected |
| F | Disposition: Follow Up |
| D | Disposition: DNC |
| W | Disposition: Wrong Number |

Disposition keys only activate after a call ends (when the toast is in disposition mode).

---

## Configuration

### `config/settings.yaml`
```yaml
workflow:
  auto_advance: false   # true = auto-dial next after disposition
  auto_hide_after: 0    # hide toast N seconds after disposition (0 = never)

teleprompter:
  enabled: true
  script: "scripts/default.md"
  stt_model: "small"    # faster-whisper: tiny/base/small/medium
```

### `config/dispositions.yaml`
```yaml
dispositions:
  - label: "Voicemail"
    hotkey: "v"
  - label: "Connected"
    hotkey: "c"
  # add your custom Aloware dispositions here
```

Labels must match your Aloware disposition names exactly.

---

## Script Format

```markdown
# Opening
Hi {contact_name}, this is {agent_name} calling from {company}.

# Pitch
...

# Objection: Not interested
...
```

Variables `{contact_name}`, `{agent_name}`, `{company}` are auto-filled from the contact card.

---

## Adding a New Template

1. Create `shadowtasker/templates/myapp.py`, subclass `BaseTemplate`
2. Override selectors and `scrape_contact` / `set_disposition` / `advance_to_next`
3. Register in `shadowtasker/templates/__init__.py`
4. Set `template: "myapp"` in `config/settings.yaml`

See `shadowtasker/templates/aloware.py` for a complete reference implementation.

---

## Architecture

```
Real display (:0)              Virtual display (:99 Xvfb)
┌──────────────────┐           ┌──────────────────────┐
│  Your work       │           │  Chromium (hidden)   │
│                  │◄─── CDP ──►  Aloware dialer      │
│  ┌────────────┐  │   events  │  MutationObserver    │
│  │ Toast (Qt) │  │           │  Network intercept   │
│  └────────────┘  │           └──────────────────────┘
└──────────────────┘
```

MIT License — contributions welcome.
