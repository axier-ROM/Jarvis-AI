"""
browser.py — Jarvis browser control module
Requires: pip install playwright && playwright install chromium
"""

import os
import base64
import json
import threading
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

# ── Brave executable path (auto-detected, override via env var) ───────────────
_BRAVE_PATHS = [
    os.environ.get("BRAVE_PATH", ""),
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    os.path.join(os.path.expanduser("~"), "AppData", "Local",
                 "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
]

def _find_brave() -> str:
    for p in _BRAVE_PATHS:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Brave not found. Set the BRAVE_PATH environment variable to your brave.exe location."
    )

# ── Singleton browser state ───────────────────────────────────────────────────
_lock      = threading.Lock()
_playwright: Playwright   = None
_browser:    Browser      = None
_context:    BrowserContext = None

def _get_context() -> BrowserContext:
    """Return (or lazily create) the shared Playwright browser context."""
    global _playwright, _browser, _context
    with _lock:
        if _context is None:
            _playwright = sync_playwright().start()
            _browser    = _playwright.chromium.launch(
                executable_path=_find_brave(),
                headless=False,
                args=["--start-maximized"],
            )
            _context = _browser.new_context(no_viewport=True)
            # Open a blank tab so there's always at least one page
            _context.new_page()
        return _context


def _active_page() -> Page:
    """Return the last focused (most recently opened) page."""
    ctx   = _get_context()
    pages = ctx.pages
    return pages[-1] if pages else ctx.new_page()


def shutdown_browser():
    """Clean up Playwright — call this on Jarvis exit."""
    global _playwright, _browser, _context
    with _lock:
        try:
            if _browser:
                _browser.close()
            if _playwright:
                _playwright.stop()
        except Exception:
            pass
        _playwright = _browser = _context = None


# ── Navigation ────────────────────────────────────────────────────────────────
def browser_navigate(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    page = _active_page()
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    return f"Navigated to {url}"


def browser_go_back() -> str:
    _active_page().go_back(wait_until="domcontentloaded", timeout=10000)
    return "Went back."


def browser_go_forward() -> str:
    _active_page().go_forward(wait_until="domcontentloaded", timeout=10000)
    return "Went forward."


def browser_reload() -> str:
    _active_page().reload(wait_until="domcontentloaded", timeout=10000)
    return "Page reloaded."


# ── Tab management ────────────────────────────────────────────────────────────
def browser_new_tab(url: str = "") -> str:
    ctx  = _get_context()
    page = ctx.new_page()
    if url:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        return f"Opened new tab at {url}."
    return "Opened new blank tab."


def browser_close_tab() -> str:
    ctx   = _get_context()
    pages = ctx.pages
    if len(pages) <= 1:
        return "Only one tab open, won't close it."
    pages[-1].close()
    return "Closed the current tab."


def browser_switch_tab(target) -> str:
    """Switch to a tab by 1-based index or keyword (next / previous / last)."""
    ctx   = _get_context()
    pages = ctx.pages
    n     = len(pages)

    if n == 0:
        return "No tabs open."

    if isinstance(target, int):
        idx = max(0, min(target - 1, n - 1))          # clamp, convert to 0-based
    elif isinstance(target, str):
        t = target.lower().strip()
        current = pages.index(_active_page()) if _active_page() in pages else n - 1
        if t in ("next", "forward"):
            idx = (current + 1) % n
        elif t in ("previous", "prev", "back"):
            idx = (current - 1) % n
        elif t in ("last", "final"):
            idx = n - 1
        else:
            try:
                idx = int(t) - 1
            except ValueError:
                return f"Don't know how to switch to tab '{target}'."
    else:
        return "Invalid tab target."

    pages[idx].bring_to_front()
    title = pages[idx].title() or f"Tab {idx + 1}"
    return f"Switched to tab {idx + 1}: {title}"


def browser_list_tabs() -> str:
    pages = _get_context().pages
    if not pages:
        return "No tabs open."
    lines = [f"{i+1}. {p.title() or p.url}" for i, p in enumerate(pages)]
    return "Open tabs:\n" + "\n".join(lines)


# ── Scrolling ─────────────────────────────────────────────────────────────────
def browser_scroll(direction: str, amount: int = 500) -> str:
    page = _active_page()
    d    = direction.lower().strip()
    if d in ("down", "bottom"):
        page.mouse.wheel(0, amount)
    elif d in ("up", "top"):
        page.mouse.wheel(0, -amount)
    elif d in ("right",):
        page.mouse.wheel(amount, 0)
    elif d in ("left",):
        page.mouse.wheel(-amount, 0)
    else:
        return f"Unknown scroll direction: {direction}"
    return f"Scrolled {d}."


# ── AI vision click ───────────────────────────────────────────────────────────
def browser_click(description: str, groq_client) -> str:
    """
    Try DOM-based clicking first (fast, reliable).
    Fall back to AI vision screenshot click if DOM search fails.
    """
    page = _active_page()

    # ── Pass 1: try to find and click via accessible text / aria label ────────
    desc_lower = description.lower().strip()
    try:
        # getByRole covers buttons, links, inputs etc.
        for role in ("link", "button", "menuitem", "tab", "option"):
            loc = page.get_by_role(role, name=desc_lower)
            if loc.count() > 0:
                loc.first.scroll_into_view_if_needed(timeout=3000)
                loc.first.click(timeout=5000)
                return f"Clicked {role} \"{description}\" via DOM."

        # getByText catches anything with matching visible text
        loc = page.get_by_text(desc_lower, exact=False)
        if loc.count() > 0:
            loc.first.scroll_into_view_if_needed(timeout=3000)
            loc.first.click(timeout=5000)
            return f"Clicked element with text \"{description}\" via DOM."
    except Exception:
        pass   # fall through to vision

    # ── Pass 2: AI vision screenshot click ───────────────────────────────────
    # Take a full-page screenshot at a fixed width so coordinates are predictable
    page.set_viewport_size({"width": 1280, "height": 900})
    screenshot_b64 = base64.b64encode(
        page.screenshot(type="jpeg", quality=85, full_page=False)
    ).decode("utf-8")

    prompt = (
        "This is a 1280x900 browser screenshot. "
        f"Find the element that best matches: \"{description}\". "
        "Reply ONLY with JSON: {\"found\": true, \"x\": <int>, \"y\": <int>} "
        "where x/y are the pixel coordinates of the element center, "
        "or {\"found\": false, \"reason\": \"...\"} if not visible."
    )

    response = groq_client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        response_format={"type": "json_object"},
        max_tokens=200,
    )

    data = json.loads(response.choices[0].message.content.strip())
    if not data.get("found", False):
        return f"Could not find \"{description}\" on the page: {data.get('reason', 'not visible')}"

    x, y = int(data["x"]), int(data["y"])
    page.mouse.click(x, y)
    return f"Clicked \"{description}\" at ({x}, {y})."


# ── Type text ─────────────────────────────────────────────────────────────────
def browser_type(text: str) -> str:
    """Type text into whatever is currently focused on the page."""
    _active_page().keyboard.type(text, delay=30)
    return f"Typed: {text}"


def browser_press_key(key: str) -> str:
    """Press a keyboard key (Enter, Escape, Tab, ArrowDown, etc.)."""
    _active_page().keyboard.press(key)
    return f"Pressed {key}."