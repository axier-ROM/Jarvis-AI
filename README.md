# Jarvis — AI Desktop Assistant

A voice- and text-driven accessibility assistant for Windows, built for the
**Youth Code × AI** hackathon (Track 03: AI That Actually Helps People).

Jarvis turns natural-language commands into real actions on your computer —
open apps, control windows, type text, create files, and search the web —
using either your voice or a keyboard. A pulsing orb in the center of the UI
shows when Jarvis is listening or thinking.

---

## Why this project?

Track 03 of the hackathon asks: *"What if you built something that genuinely
made someone's life better?"* Jarvis is built around accessibility:

- **Voice-first** — for people who can't or don't want to use a mouse.
- **Text fallback** — every voice command can also be typed.
- **High-contrast dark UI** — clear focus states, color-coded status.
- **Pause button** — disable the mic instantly when you need privacy.
- **Local command router** — works for simple commands even without an API key.
- **British AI voice** — calm, professional, easy to understand.

---

## Features

| Capability | Voice | Text |
|---|---|---|
| Open apps (fuzzy match across Start Menu + Desktop) | ✅ | ✅ |
| Close apps | ✅ | ✅ |
| Window control (snap / minimize / maximize / close / switch / desktop) | ✅ | ✅ |
| Type text into the focused window | ✅ | ✅ |
| Create files (with auto-mkdir for parent directories) | ✅ | ✅ |
| Search the web (opens default browser) | ✅ | ✅ |
| Local commands (hello, time, date, help) — no API needed | ✅ | ✅ |
| Export chat history to a text file | ✅ | ✅ |
| Persistent API key in `~/.jarvis_config.json` | — | ✅ |

---

## Requirements

- **Windows 10 / 11**
- **Python 3.10+**
- A microphone (optional, for voice input)
- A free **Groq API key** from <https://console.groq.com/keys>
  (only needed for AI-powered commands — local commands work without one)

Python packages:

```
customtkinter
SpeechRecognition
edge-tts
groq
psutil
pyautogui
```

(Note: `customtkinter` is no longer required at runtime — the UI is built
with plain `tkinter` for solid colors and no rendering artifacts. The package
is still listed in case you bring back a CTk-based component.)

Plus `PyAudio` for microphone input (see troubleshooting).

---

## Install & Run

```powershell
# 1. Clone
git clone https://github.com/<your-username>/jarvis.git
cd jarvis

# 2. (Recommended) create a venv
python -m venv .venv
.\.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your Groq API key (one of three ways):
#    a) Settings menu in the app → "Set API Key…"
#    b) environment variable:
$env:GROQ_API_KEY=***    c) paste it into the Settings menu once it's running

# 5. Launch
python jarvis.py
```

---

## Voice commands — quick start

Click the pink microphone button and try:

- *"Hello"*
- *"What time is it?"*
- *"Open notepad"*
- *"Search for python tutorials"*
- *"Close chrome"*

You can also type any of these into the chat box — there's no difference
in what Jarvis can do.

Click the **⏸** button next to the mic to pause listening.

---

## UI overview

```
┌────────────────────────────────────────────────────┐
│ File   Settings   Help                    [_ □ ✕]  │
├────────────────────────────────────────────────────┤
│                   ⚡  Jarvis                        │
│   Try: "open notepad" · "what time is it" · …      │
│                                                    │
│                       ◉  ← animated orb             │
│                  (rings pulse when AI speaks)      │
│                                                    │
│ ●  Ready                                           │
│ ┌────────────────────────────────────────────────┐ │
│ │ [you] hello                                     │ │
│ │ [jarvis] Hello. How can I help?                 │ │
│ └────────────────────────────────────────────────┘ │
│ ┌──────┬─────┬──────────────────────┬─────────────┐│
│ │  🎙  │  ⏸  │ Type a command…      │  SEND   ➤  ││
│ └──────┴─────┴──────────────────────┴─────────────┘│
└────────────────────────────────────────────────────┘
```

- **Orb** is blue when idle, purple with expanding rings when AI is thinking/speaking.
- **Listening indicator** above the orb animates dots while the mic is active.
- **Pause button** (⏸) toggles the mic on/off; turns yellow when paused.
- **Status bar** uses color: green = ready, orange = thinking, red = error.

---

## Configuration

The app stores user config in `~/.jarvis_config.json`:

```json
{
  "api_key": "gsk_..."
}
```

You can edit this file directly via **Settings → Open Config File**, or
delete it to reset.

---

## Architecture

```
jarvis.py  (single file, ~780 lines)
├── Configuration         — colors, paths, model name, config file path
├── App Index             — fuzzy matcher over Start Menu + Desktop shortcuts
├── Tools                 — pure functions, one per Jarvis capability
├── AI Core               — Groq chat call, JSON tool-call dispatch
├── Local Router          — handles simple commands without an API call
├── Speech (TTS)          — async edge-tts loop with British voice
├── Speech (STT)          — voice input via SpeechRecognition + Google
└── UI (Tk)               — main window: menu, orb, chat, input, buttons
```

The `TOOLS` dict is the single source of truth for what Jarvis can do. The
`SYSTEM_PROMPT` is built from it so adding a new tool only requires writing
the function and registering it.

---

## Safety

- Local commands only open/close apps, control windows, type into the focused
  field, write to files you specify, or open a browser — all reversible.
- No code execution, no shutdown, no admin operations.
- API key is stored locally only; never transmitted except to Groq's API.

---

## Troubleshooting

**`PyAudio` install fails on Windows**

```
pip install pipwin
pipwin install pyaudio
```

Or download the matching wheel from
<https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio>.

**`SpeechRecognition` is missing**

Voice input is optional. The app detects this and shows a status message;
you can keep using Jarvis via text input.

**The microphone button does nothing**

Check Windows Settings → Privacy → Microphone → allow desktop apps.

**`No module named 'customtkinter'` (or any other package)**

You probably have multiple Python installs. Use the same `python` you used
for `pip install -r requirements.txt`.

**The orb doesn't animate**

That's intentional — the orb only animates when Jarvis is actively thinking
or speaking. At rest it's a solid blue circle.

---

## License

MIT.