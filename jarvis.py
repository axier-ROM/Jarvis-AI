"""
Jarvis — Simple AI desktop assistant
Opens/closes apps, controls windows, types text, creates files, searches web.
"""

import difflib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import asyncio
import tempfile
import customtkinter as ctk
import edge_tts
import pyautogui
import psutil
import ctypes
from groq import Groq

try:
    import speech_recognition as sr
except ImportError:
    sr = None

APP_NAME = "Jarvis"
GROQ_MODEL = "llama-3.3-70b-versatile"

# ── App index ────────────────────────────────────────────────────────────────
BUILTIN = {
    "notepad": "notepad.exe", "calculator": "calc.exe",
    "task manager": "taskmgr.exe", "file explorer": "explorer.exe",
    "settings": "ms-settings:", "command prompt": "cmd.exe",
    "chrome": "chrome.exe", "firefox": "firefox.exe",
    "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    "spotify": "spotify.exe", "discord": "discord.exe",
}
SCAN_DIRS = [
    os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"),
                 "Microsoft", "Windows", "Start Menu"),
    os.path.join(os.path.expanduser("~"), "AppData", "Roaming",
                 "Microsoft", "Windows", "Start Menu"),
    os.path.join(os.path.expanduser("~"), "Desktop"),
]


def build_index():
    idx = dict(BUILTIN)
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.lower().endswith((".lnk", ".exe")):
                    idx.setdefault(os.path.splitext(f)[0].lower(),
                                   os.path.join(root, f))
    return idx


APPS = build_index()


def find_app(name):
    name = name.lower().strip()
    for k in APPS:
        if name in k or k in name:
            return k
    m = difflib.get_close_matches(name, APPS, n=1, cutoff=0.5)
    return m[0] if m else None


# ── Tools ────────────────────────────────────────────────────────────────────
def open_app(a):
    key = find_app(a.get("app_name", ""))
    if not key:
        return f"Couldn't find '{a.get('app_name','')}'."
    try:
        os.startfile(APPS[key])
        return f"Opened {key}."
    except Exception as e:
        return f"Failed: {e}"


def close_app(a):
    key = find_app(a.get("app_name", ""))
    if not key:
        return f"Couldn't find '{a.get('app_name','')}'."
    exe = os.path.basename(APPS[key]).replace(".lnk", ".exe").lower()
    target = exe if exe.endswith(".exe") else key.split()[0] + ".exe"
    killed = 0
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n == target or n.startswith(key.split()[0]):
            try:
                p.terminate(); killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return f"Closed {key}." if killed else f"{key} wasn't running."


def manage_window(a):
    moves = {
        "left": ("win", "left"), "right": ("win", "right"),
        "maximize": ("win", "up"),
        "minimize": ("win", "down"), "close": ("alt", "f4"),
        "switch": ("alt", "tab"), "desktop": ("win", "d"),
    }
    cmd = a.get("action", "").lower().strip()
    if cmd not in moves:
        return f"Unknown action '{cmd}'. Try: left, right, maximize, minimize, close, switch, desktop."
    pyautogui.hotkey(*moves[cmd])
    return f"Window: {cmd}"


def type_text(a):
    text = a.get("text", "")
    if not text:
        return "No text given."
    pyautogui.write(text, interval=0.02)
    return f"Typed {len(text)} characters."


def create_file(a):
    path = a.get("path", "").strip()
    content = a.get("content", "")
    if not path:
        return "No path given."
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Created {path}"
    except Exception as e:
        return f"Failed: {e}"


def web_search(a):
    q = a.get("query", "").strip()
    if not q:
        return "No query."
    webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(q)}")
    return f"Searched: {q}"


TOOLS = {
    "open_app": open_app, "close_app": close_app,
    "manage_window": manage_window, "type_text": type_text,
    "create_file": create_file, "search_the_web": web_search,
}


def tool_desc():
    return "\n".join([
        "- open_app(app_name): fuzzy match over installed apps",
        "- close_app(app_name)",
        "- manage_window(action): left|right|maximize|minimize|close|switch|desktop",
        "- type_text(text): types into the focused window",
        "- create_file(path, content): writes a file, creates parent dirs",
        "- search_the_web(query): opens browser with Google search",
    ])


SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        f"You are {APP_NAME}, a concise desktop AI assistant. "
        "Reply ONLY with valid JSON — no prose:\n"
        '{"tool": "name_or_none", "args": {}, "reply": "short reply, max 1 sentence"}\n'
        "Tools:\n" + tool_desc() + "\n"
        "Use 'none' for pure conversation. Keep replies under 1 sentence."
    ),
}


# ── AI ───────────────────────────────────────────────────────────────────────
groq_client = None


def set_key(key):
    global groq_client
    if key and len(key.strip()) > 10:
        groq_client = Groq(api_key=key.strip())
        return True
    groq_client = None
    return False


def process(cmd, ui):
    ui.log("you", cmd)
    ui._show_listening_indicator(False)

    # Try local command first — no API needed
    local_reply = try_local(cmd)
    if local_reply is not None:
        ui.log(APP_NAME.lower(), local_reply); ui.say(local_reply, "green")
        ui.set_busy(True)
        _speak_async(local_reply)
        # speak() takes ~1s; turn off busy after
        ui.after(1500, lambda: ui.set_busy(False))
        return

    if not groq_client:
        ui.log("error", "No API key — try: hello, time, open notepad, search python")
        ui.say("No API key. Try a simple command instead.", "red")
        _speak_async("No API key configured. Try a simple command like time, or open notepad.")
        return

    ui.set_busy(True)
    try:
        r = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[SYSTEM_PROMPT, {"role": "user", "content": cmd}],
            response_format={"type": "json_object"},
        )
        data = json.loads(r.choices[0].message.content or "{}")
        tool, args, reply = (data.get("tool") or "none").lower(), data.get("args") or {}, data.get("reply") or "Done."
        if tool in TOOLS:
            result = TOOLS[tool](args)
            ui.log("tool", f"{tool} → {result}")
        ui.log(APP_NAME.lower(), reply); ui.say(reply, "green")
        _speak_async(reply)
        # Keep the orb animated while speaking (~2s for short replies)
        ui.after(max(1500, len(reply) * 80), lambda: ui.set_busy(False))
    except Exception as e:
        ui.log("error", str(e)); ui.say("Error.", "red")
        _speak_async("I encountered an error.")
        ui.set_busy(False)


# ── Local command router (works without API key) ─────────────────────────────
LOCAL_COMMANDS = {
    "hello":      lambda ui: "Hello. How can I help?",
    "hi":         lambda ui: "Hi there.",
    "hey":        lambda ui: "Hey. What do you need?",
    "thanks":     lambda ui: "You're welcome.",
    "thank you":  lambda ui: "Anytime.",
    "how are you":lambda ui: "Functioning within normal parameters.",
    "what time is it": lambda ui: __import__("time").strftime("It's %I:%M %p."),
    "time":       lambda ui: __import__("time").strftime("It's %I:%M %p."),
    "date":       lambda ui: __import__("time").strftime("Today is %A, %B %d."),
    "help":       lambda ui: ("I can open apps, close apps, control windows, type text, "
                              "create files, search the web, and tell you the time or date."),
    "what can you do": lambda ui: ("I can open apps, close apps, control windows, type text, "
                                    "create files, search the web, and tell you the time or date."),
}


def try_local(text):
    """If the command is something simple we can handle without AI, do it.
    Returns a reply string, or None to fall through to the API."""
    t = text.lower().strip()
    if t in LOCAL_COMMANDS:
        return LOCAL_COMMANDS[t](None)

    # Pattern: "open X" / "close X" / "launch X" — local fuzzy match
    for prefix, tool in [("open ", "open_app"), ("launch ", "open_app"),
                         ("start ", "open_app"), ("close ", "close_app"),
                         ("quit ", "close_app"), ("exit ", "close_app"),
                         ("search ", "search_the_web"), ("google ", "search_the_web")]:
        if t.startswith(prefix):
            arg = t[len(prefix):].strip()
            if tool in TOOLS:
                if tool == "search_the_web":
                    result = TOOLS[tool]({"query": arg})
                else:
                    result = TOOLS[tool]({"app_name": arg})
                return result
    return None


# ── Voice ────────────────────────────────────────────────────────────────────
_tts_loop = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(_tts_loop), _tts_loop.run_forever()),
                 daemon=True).start()


def speak(text):
    if not text: return
    print(f"{APP_NAME}: {text}")
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False); f.close()
    try:
        asyncio.run_coroutine_threadsafe(
            edge_tts.Communicate(text, "en-GB-RyanNeural", rate="+10%").save(f.name),
            _tts_loop,
        ).result(timeout=15)
        ctypes.windll.winmm.mciSendStringW(
            f'open "{f.name}" type mpegvideo alias jv', None, 0, 0)
        ctypes.windll.winmm.mciSendStringW("play jv wait", None, 0, 0)
        ctypes.windll.winmm.mciSendStringW("close jv", None, 0, 0)
    except Exception as e:
        print(f"[TTS] {e}")
    finally:
        try: os.remove(f.name)
        except OSError: pass


def _speak_async(text):
    """Run speak() in a thread so it doesn't block the UI."""
    threading.Thread(target=speak, args=(text,), daemon=True).start()


def listen(ui):
    """Listen for voice input and route it back to the UI."""
    rec = sr.Recognizer()  # type: ignore[union-attr]
    rec.pause_threshold = 0.8
    try:
        with sr.Microphone() as mic:  # type: ignore[union-attr]
            audio = rec.listen(mic, timeout=8)
        text = rec.recognize_google(audio).lower()
        # Run command on UI thread
        ui.after(0, ui._on_voice_result, text)
    except sr.WaitTimeoutError:  # type: ignore[union-attr]
        ui.after(0, ui._on_voice_error, "No speech detected.")
    except sr.UnknownValueError:  # type: ignore[union-attr]
        ui.after(0, ui._on_voice_error, "Couldn't understand.")
    except Exception as e:
        ui.after(0, ui._on_voice_error, str(e))


# ── UI (plain tkinter — no CTk rendering artifacts) ─────────────────────────
import json
import tkinter as tk
from tkinter import scrolledtext, font as tkfont, messagebox, simpledialog

BG       = "#1A1D24"
PANEL    = "#252932"
PANEL_2  = "#2E333D"
BORDER   = "#3A3F4B"
TEXT     = "#FFFFFF"
DIM      = "#B8C0CC"
ACCENT   = "#4F8CFF"
SUCCESS  = "#7BD389"
WARNING  = "#F39C12"
DANGER   = "#FF6B6B"
LISTEN   = "#FF5C7A"
ORB_IDLE = "#4F8CFF"
ORB_BUSY = "#7C5CFF"
INPUT_BG = "#15171C"

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".jarvis_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


class OrbVisualizer:
    """A canvas-based ring around the orb that pulses when AI is busy/speaking."""

    def __init__(self, canvas, cx, cy, r):
        self.canvas = canvas
        self.cx, self.cy, self.r = cx, cy, r
        self.running = False
        self.active = False
        self._step = 0

    def start(self):
        if self.running: return
        self.running = True
        self._tick()

    def stop(self):
        self.running = False

    def set_active(self, active):
        self.active = active

    def _tick(self):
        if not self.running: return
        self._step = (self._step + 1) % 360
        self.canvas.delete("orb")
        if self.active:
            # Animated rings expanding outward
            for i in range(3):
                offset = (self._step + i * 40) % 180
                radius = self.r + 10 + offset
                alpha_hex = max(40, 255 - offset)
                color = f"#{alpha_hex:02x}{int(alpha_hex*0.55):02x}ff"
                self.canvas.create_oval(
                    self.cx - radius, self.cy - radius,
                    self.cx + radius, self.cy + radius,
                    outline=color, width=2, tags="orb",
                )
            # Inner pulsing core
            pulse = abs(180 - self._step) / 180.0  # 0..1
            core_r = self.r - 4 + int(pulse * 6)
            self.canvas.create_oval(
                self.cx - core_r, self.cy - core_r,
                self.cx + core_r, self.cy + core_r,
                fill=ORB_BUSY, outline="", tags="orb",
            )
        else:
            # Idle: solid orb with subtle glow
            self.canvas.create_oval(
                self.cx - self.r - 6, self.cy - self.r - 6,
                self.cx + self.r + 6, self.cy + self.r + 6,
                fill="#1F2640", outline="", tags="orb",
            )
            self.canvas.create_oval(
                self.cx - self.r, self.cy - self.r,
                self.cx + self.r, self.cy + self.r,
                fill=ORB_IDLE, outline="", tags="orb",
            )
        self.canvas.after(40, self._tick)


class Jarvis(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — AI Assistant")
        self.geometry("760x720")
        self.configure(bg=BG)
        self.minsize(680, 640)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # Fonts
        self.f_title = tkfont.Font(family="Segoe UI", size=22, weight="bold")
        self.f_sub   = tkfont.Font(family="Segoe UI", size=11)
        self.f_body  = tkfont.Font(family="Segoe UI", size=11)
        self.f_small = tkfont.Font(family="Segoe UI", size=10)
        self.f_btn   = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.f_mic   = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.f_stat  = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.f_orb   = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        # State
        self._mic_paused = False
        self._is_busy = False
        self._history_rows = []  # for export
        self._config = load_config()
        env_key = os.environ.get("GROQ_API_KEY") or self._config.get("api_key", "")
        if env_key:
            set_key(env_key)

        self._build_menu()
        self._build()
        self._show_listening_indicator(False)

    # ── Menu bar ─────────────────────────────────────────────────────────
    def _build_menu(self):
        menubar = tk.Menu(self, tearoff=0)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Export Chat History…", command=self._export_history)
        m_file.add_separator()
        m_file.add_command(label="Quit", command=self.destroy)
        menubar.add_cascade(label="File", menu=m_file)

        m_config = tk.Menu(menubar, tearoff=0)
        m_config.add_command(label="Set API Key…", command=self._set_api_key)
        m_config.add_command(label="Clear API Key", command=self._clear_api_key)
        m_config.add_separator()
        m_config.add_command(label="Open Config File", command=self._open_config_file)
        menubar.add_cascade(label="Settings", menu=m_config)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=m_help)

        self.config(menu=menubar)

    # ── Layout ───────────────────────────────────────────────────────────
    def _build(self):
        # Header (compact)
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", pady=(12, 0))
        tk.Label(header, text=f"⚡  {APP_NAME}", font=self.f_title,
                 bg=BG, fg=TEXT).pack()
        tk.Label(header, text='Try: "open notepad"  ·  "what time is it"  ·  "search python"',
                 font=self.f_sub, bg=BG, fg=DIM).pack(pady=(2, 0))

        # Orb area
        orb_frame = tk.Frame(self, bg=BG)
        orb_frame.pack(fill="x", pady=(10, 4))
        self.orb_canvas = tk.Canvas(orb_frame, bg=BG, height=180,
                                    highlightthickness=0)
        self.orb_canvas.pack(fill="x")
        # Center the orb
        self.orb_canvas.bind("<Configure>", lambda _: self._layout_orb())
        self.visualizer = OrbVisualizer(self.orb_canvas, 380, 90, 50)

        # Listening indicator above orb (small text + animated dots)
        self.listen_indicator = tk.Label(orb_frame, text="", font=self.f_orb,
                                         bg=BG, fg=LISTEN)
        self.listen_indicator.pack()
        self._dot_step = 0
        self._dots_anim_id = None

        # Status row
        self.status_var = tk.StringVar(value="●  Ready")
        self.status_lbl = tk.Label(self, textvariable=self.status_var,
                                   font=self.f_stat, bg=BG, fg=SUCCESS, anchor="w")
        self.status_lbl.pack(fill="x", padx=24, pady=(4, 4))

        # Chat log (smaller)
        log_frame = tk.Frame(self, bg=PANEL, highlightbackground=BORDER,
                            highlightthickness=1, bd=0)
        log_frame.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        self.log_box = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=self.f_body,
            bg=PANEL, fg=TEXT, insertbackground=TEXT,
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=10, pady=8, state="disabled", height=6,
        )
        self.log_box.pack(fill="both", expand=True, padx=1, pady=1)

        # Input bar
        input_bar = tk.Frame(self, bg=PANEL, highlightbackground=BORDER,
                             highlightthickness=1, bd=0)
        input_bar.pack(fill="x", padx=18, pady=(0, 16))

        self.mic_btn = tk.Button(
            input_bar, text="🎙", font=self.f_mic,
            bg=LISTEN, fg="#FFFFFF", activebackground="#FF3A5C",
            activeforeground="#FFFFFF", relief="flat", bd=0,
            width=3, height=1, cursor="hand2",
            command=self._on_mic_click,
        )
        self.mic_btn.pack(side="left", padx=(10, 6), pady=10)

        self.pause_btn = tk.Button(
            input_bar, text="⏸", font=self.f_btn,
            bg=PANEL_2, fg=TEXT, activebackground=BORDER,
            activeforeground=TEXT, relief="flat", bd=0,
            width=3, height=1, cursor="hand2",
            command=self._toggle_pause,
        )
        self.pause_btn.pack(side="left", padx=(0, 6), pady=10)

        entry_wrap = tk.Frame(input_bar, bg=INPUT_BG)
        entry_wrap.pack(side="left", fill="both", expand=True, padx=4, pady=10)

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            entry_wrap, textvariable=self.entry_var,
            font=self.f_body, bg=INPUT_BG, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0,
            highlightthickness=0,
        )
        self.entry.pack(fill="both", expand=True, padx=12, ipady=6)
        self._ph_text = "Type a command or click the mic…"
        self._ph_color = "#7A8290"
        self._ph_active = True
        self.entry.insert(0, self._ph_text)
        self.entry.configure(fg=self._ph_color)
        self.entry.bind("<FocusIn>", self._ph_clear)
        self.entry.bind("<FocusOut>", self._ph_restore)
        self.entry.bind("<Return>", lambda _: self.run_cmd(self.entry_var.get()))

        self.send_btn = tk.Button(
            input_bar, text="SEND  ➤", font=self.f_btn,
            bg=ACCENT, fg="#FFFFFF", activebackground="#7C5CFF",
            activeforeground="#FFFFFF", relief="flat", bd=0,
            padx=14, pady=6, cursor="hand2",
            command=lambda: self.run_cmd(self.entry_var.get()),
        )
        self.send_btn.pack(side="right", padx=(6, 10), pady=10)

        # Hover effects
        self.send_btn.bind("<Enter>", lambda _: self.send_btn.configure(bg="#7C5CFF"))
        self.send_btn.bind("<Leave>", lambda _: self.send_btn.configure(bg=ACCENT))
        self.mic_btn.bind("<Enter>", lambda _: self.mic_btn.configure(bg="#FF3A5C") if not self._mic_paused else None)
        self.mic_btn.bind("<Leave>", lambda _: self.mic_btn.configure(bg=LISTEN) if not self._mic_paused else self.mic_btn.configure(bg=PANEL_2))

        # Start the visualizer (idle by default)
        self.visualizer.start()

        # Greet
        if groq_client:
            self.say("Ready.", "green")
        else:
            self.say("Ready — no API key. Use Settings → Set API Key, or try: hello, time, open notepad.", "orange")

    def _layout_orb(self):
        """Recenter the orb whenever the canvas is resized."""
        w = self.orb_canvas.winfo_width()
        h = self.orb_canvas.winfo_height()
        self.visualizer.cx = w // 2
        self.visualizer.cy = h // 2

    # ── Listening indicator ──────────────────────────────────────────────
    def _show_listening_indicator(self, on: bool, text: str = ""):
        if on:
            self._dot_step = 0
            self._animate_dots(text or "Listening")
            self.mic_btn.configure(bg=LISTEN)
        else:
            if self._dots_anim_id:
                self.after_cancel(self._dots_anim_id)
                self._dots_anim_id = None
            self.listen_indicator.configure(text="")
            if not self._mic_paused:
                self.mic_btn.configure(bg=LISTEN)
            else:
                self.mic_btn.configure(bg=PANEL_2)

    def _animate_dots(self, base_text: str):
        dots = "." * ((self._dot_step % 3) + 1)
        self.listen_indicator.configure(text=f"{base_text}{dots}")
        self._dot_step += 1
        self._dots_anim_id = self.after(400, self._animate_dots, base_text)

    # ── Mic / pause ──────────────────────────────────────────────────────
    def _on_voice_result(self, text):
        """Called on UI thread when voice recognition succeeds."""
        self._show_listening_indicator(False)
        if self._mic_paused:
            return
        self.run_cmd(text)

    def _on_voice_error(self, msg):
        """Called on UI thread when voice recognition fails."""
        self._show_listening_indicator(False)
        if "No speech" in msg or "understand" in msg:
            self.say(f"Voice: {msg}", "orange")
        else:
            self.say(f"Mic error: {msg}", "red")

    def _on_mic_click(self):
        if self._mic_paused:
            return
        if sr is None:
            self.say("SpeechRecognition not installed — use text input.", "red")
            return
        if self._is_busy:
            return  # don't interrupt AI while it's working
        self._show_listening_indicator(True, "Listening")
        threading.Thread(target=listen, args=(self,), daemon=True).start()

    def _toggle_pause(self):
        self._mic_paused = not self._mic_paused
        if self._mic_paused:
            self.pause_btn.configure(text="▶", bg=WARNING)
            self.mic_btn.configure(bg=PANEL_2)
            self._show_listening_indicator(False)
            self.say("Microphone paused.", "orange")
        else:
            self.pause_btn.configure(text="⏸", bg=PANEL_2)
            self.mic_btn.configure(bg=LISTEN)
            self.say("Microphone resumed.", "green")

    # ── Menu actions ─────────────────────────────────────────────────────
    def _set_api_key(self):
        current = self._config.get("api_key", "")
        # Show masked current value
        if current:
            prompt = f"Current key: {'•' * 8}{current[-4:]}\n\nPaste new Groq API key (console.groq.com):"
        else:
            prompt = "Paste your Groq API key (get one at console.groq.com):"
        key = simpledialog.askstring(f"{APP_NAME} — API Key", prompt,
                                     show="•", parent=self)
        if key is None:
            return
        key = key.strip()
        if not key:
            self.say("Cancelled.", "orange")
            return
        if set_key(key):
            self._config["api_key"] = key
            save_config(self._config)
            self.say("API key saved.", "green")
            messagebox.showinfo(APP_NAME, "API key saved to ~/.jarvis_config.json", parent=self)
        else:
            self.say("That key looks invalid.", "red")

    def _clear_api_key(self):
        if not messagebox.askyesno(APP_NAME, "Clear the saved API key?", parent=self):
            return
        global groq_client
        groq_client = None
        self._config.pop("api_key", None)
        save_config(self._config)
        self.say("API key cleared.", "orange")

    def _open_config_file(self):
        # Ensure file exists
        if not os.path.exists(CONFIG_PATH):
            save_config(self._config)
        try:
            if sys.platform.startswith("win"):
                os.startfile(CONFIG_PATH)  # type: ignore[attr-defined]
            else:
                webbrowser.open(f"file://{CONFIG_PATH}")
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Couldn't open config: {e}", parent=self)

    def _export_history(self):
        if not self._history_rows:
            self.say("Nothing to export yet.", "orange")
            return
        path = os.path.join(os.path.expanduser("~"), "Desktop",
                            f"jarvis_history_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{APP_NAME} conversation history\n")
                f.write(f"Exported {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("\n\n".join(self._history_rows))
            self.say(f"Exported to {os.path.basename(path)}", "green")
        except Exception as e:
            self.say(f"Export failed: {e}", "red")

    def _show_about(self):
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} — AI Desktop Assistant\n\n"
            "Voice- and text-driven accessibility tool.\n"
            "Built for Youth Code × AI hackathon.\n\n"
            f"Config: {CONFIG_PATH}\n"
            f"Indexed: {len(APPS)} apps",
            parent=self,
        )

    # ── Placeholder behavior ─────────────────────────────────────────────
    def _ph_clear(self, _=None):
        if self._ph_active and self.entry_var.get() == self._ph_text:
            self.entry_var.set("")
            self.entry.configure(fg=TEXT)
            self._ph_active = False

    def _ph_restore(self, _=None):
        if not self.entry_var.get():
            self.entry_var.set(self._ph_text)
            self.entry.configure(fg=self._ph_color)
            self._ph_active = True

    # ── UI helpers called by core ────────────────────────────────────────
    def log(self, who, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{who}] {text}\n\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self._history_rows.append(f"[{who}] {text}")
        # Cap history to avoid memory growth
        if len(self._history_rows) > 500:
            self._history_rows = self._history_rows[-500:]

    def say(self, text, color="gray"):
        colors = {"red": DANGER, "green": SUCCESS, "orange": WARNING,
                  "gray": DIM, "yellow": WARNING}
        self.status_var.set(f"●  {text}")
        self.status_lbl.configure(fg=colors.get(color, color))

    def set_busy(self, busy: bool):
        """Toggle orb between idle (blue) and busy/speaking (purple + rings)."""
        self._is_busy = busy
        self.visualizer.set_active(busy)
        if busy:
            self.say("Thinking…", "orange")
        elif groq_client:
            self.say("Ready.", "green")
        else:
            self.say("Ready.", "green")

    def run_cmd(self, text):
        text = text.strip()
        if not text: return
        self.entry_var.set("")
        self._ph_restore()
        if text.lower() in ("quit", "exit"):
            self.destroy(); return
        threading.Thread(target=process, args=(text, self), daemon=True).start()


if __name__ == "__main__":
    print(f"[{APP_NAME}] {len(APPS)} apps indexed.")
    Jarvis().mainloop()