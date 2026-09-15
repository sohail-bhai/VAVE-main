"""
Line icons for the desktop UI.

CustomTkinter draws images, not vectors, so each icon is kept here as SVG and
rasterised on demand at the exact size and colour a widget asks for. That way
one definition serves a 16px muted sidebar glyph and a 22px accent glyph on a
card without shipping two files, and nothing looks soft on a high-DPI screen.

Rendering needs cairosvg. When it is missing every lookup returns None and the
callers fall back to their text label, so the app still runs on a machine that
only has Pillow.
"""
from __future__ import annotations

import io
import logging
import threading

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the machine, not on our code
    import cairosvg
except Exception:  # ImportError, or a missing libcairo
    cairosvg = None

try:
    from PIL import Image
except Exception:
    Image = None

# Every icon is a 24x24 stroked path, drawn on the same grid so they sit
# together evenly. Only the path data differs.
_PATHS = {
    "home": "M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5",
    "devices": "M3 5.5h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1M6 18h6M9 14.5V18M18.5 8.5h3a.5.5 0 0 1 .5.5v10a.5.5 0 0 1-.5.5h-3a.5.5 0 0 1-.5-.5V9a.5.5 0 0 1 .5-.5",
    "files": "M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "google": "M21 12.2c0 5-3.6 8.3-8.8 8.3A8.5 8.5 0 1 1 12.2 3.5c2.3 0 4.2.9 5.6 2.2l-2.4 2.3A4.9 4.9 0 0 0 12.2 6.7a5.6 5.6 0 1 0 0 11.2c3.4 0 4.9-2 5.2-3.9h-5.2v-3h8.6z",
    "web": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c2.5 2.4 3.8 5.4 3.8 9s-1.3 6.6-3.8 9c-2.5-2.4-3.8-5.4-3.8-9S9.5 5.4 12 3",
    "activity": "M3 12.5h4L9.5 6l5 12L17 12.5h4",
    "settings": "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.3 7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.9-1.2V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16M21 21l-4.3-4.3",
    "bell": "M18 9a6 6 0 1 0-12 0c0 5-2 6.5-2 6.5h16S18 14 18 9M13.7 19a2 2 0 0 1-3.4 0",
    "mic": "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3M5.5 11.5A6.5 6.5 0 0 0 18.5 11.5M12 18v3",
    "send": "M12 20V5M6 11l6-6 6 6",
    "check": "M4.5 12.5 9.5 17.5 19.5 7",
    "phone": "M8 2.5h8a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V4A1.5 1.5 0 0 1 8 2.5M11 18.5h2",
    "monitor": "M4 4.5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1M8.5 20h7M12 15.5V20",
    "globe": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3.5 9h17M3.5 15h17M12 3c2.3 2.4 3.5 5.4 3.5 9s-1.2 6.6-3.5 9c-2.3-2.4-3.5-5.4-3.5-9S9.7 5.4 12 3",
    "sparkle": "M12 3.5 13.8 9 19.5 10.8 13.8 12.6 12 18 10.2 12.6 4.5 10.8 10.2 9zM18.5 16.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z",
    "shield": "M12 3 20 6v6c0 4.5-3.2 7.7-8 9-4.8-1.3-8-4.5-8-9V6z",
    "clock": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7.5V12l3 1.8",
    "arrow-right": "M5 12h13M13 6.5 18.5 12 13 17.5",
    "plus": "M12 5v14M5 12h14",
    "x": "M6 6l12 12M18 6 6 18",
    "mail": "M3.5 6.5h17a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1M2.8 7.5 12 13.5l9.2-6",
    "calendar": "M4.5 5.5h15a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1M8 3v5M16 3v5M3.5 10.5h17",
    "document": "M6 3h7.5L19 8.5V21H6zM13.5 3v5.5H19M9 13h7M9 16.5h7",
    "sheet": "M4.5 4.5h15v15h-15zM4.5 9.5h15M4.5 14.5h15M9.5 4.5v15M14.5 4.5v15",
    "slides": "M3.5 4.5h17v11h-17zM12 15.5V19M8.5 21l3.5-2 3.5 2",
    "folder": "M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
    "file": "M6.5 3h7L18 7.5V21h-11.5zM13.5 3v4.5H18",
    "memory": "M9.5 3.5A3 3 0 0 0 6.5 7a3 3 0 0 0-2 5.4A3 3 0 0 0 6.8 18a3 3 0 0 0 5.7-1.3v-13a3 3 0 0 0-3-.2M14.5 3.5a3 3 0 0 1 3 3.5 3 3 0 0 1 2 5.4A3 3 0 0 1 17.2 18a3 3 0 0 1-5.7-1.3",
    "link": "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 1 0-5.7-5.7l-1.5 1.5M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 1 0 5.7 5.7l1.5-1.5",
    "helper": "M8.5 7.5h7a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2M12 4.5v3M9.5 12h.01M14.5 12h.01M3.5 11v4M20.5 11v4",
    "stop": "M9 9h6v6H9zM12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
    "warning": "M12 4 21 19.5H3zM12 10v4.5M12 17h.01",
    "bulb": "M9.5 18h5M10 21h4M12 3a6 6 0 0 0-3.5 10.8c.6.5 1 1.3 1 2.2h5c0-.9.4-1.7 1-2.2A6 6 0 0 0 12 3",
    "play": "M8 5.5 18 12 8 18.5z",
    "refresh": "M20 12a8 8 0 1 1-2.4-5.7M20 3.5V9h-5.5",
    "user": "M12 12.5a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4.5 20.5a7.5 7.5 0 0 1 15 0",
    "lock": "M6.5 10.5h11a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1M8.5 10.5V7.5a3.5 3.5 0 1 1 7 0v3",
    "download": "M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4.5 19.5h15",
    "trash": "M4.5 6.5h15M9.5 6.5V4.5h5v2M6.5 6.5 7.5 20.5h9l1-14M10 10.5v6M14 10.5v6",
}

# The two sizes nearly every widget asks for.
ICON_PREWARM_SMALL = 15
ICON_PREWARM_BASE = 17

# One rendered bitmap per (name, size, colour, width). Icons are re-requested
# on every page rebuild, and rasterising is far slower than a dict lookup.
_cache: dict[tuple, object] = {}


def available() -> bool:
    """True when icons can actually be drawn on this machine."""
    return cairosvg is not None and Image is not None


def _svg(name: str, color: str, stroke_width: float) -> bytes:
    path = _PATHS[name]
    filled = name in {"google", "sparkle", "shield", "play"}
    paint = (f'fill="{color}" stroke="none"' if filled
             else f'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
                  'stroke-linecap="round" stroke-linejoin="round"')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<path d="{path}" {paint}/></svg>'
    ).encode("utf-8")


def image(name: str, size: int = 18, color: str = "#1A1B1E", stroke_width: float = 1.7):
    """Return a CTkImage for one icon, or None when it cannot be drawn.

    Callers must keep the returned object alive: Tk drops an image the moment
    Python stops referencing it, and the widget then shows nothing.
    """
    if not available() or name not in _PATHS:
        return None

    key = (name, size, color, stroke_width)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    try:
        import customtkinter as ctk

        # Rasterise at 2x so the icon stays sharp where Tk scales it up.
        png = cairosvg.svg2png(bytestring=_svg(name, color, stroke_width),
                               output_width=size * 2, output_height=size * 2)
        pil = Image.open(io.BytesIO(png)).convert("RGBA")
        rendered = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
    except Exception:
        logger.debug("Could not render icon %r", name, exc_info=True)
        return None

    _cache[key] = rendered
    return rendered


def reset_cache():
    """Drop every cached bitmap.

    Tk deletes a PhotoImage with the root that created it, so the cache must
    not outlive that root: a second window reusing a cached icon would render
    from a dead bitmap ("image doesn't exist"). The dashboard clears this when
    it is destroyed, which keeps repeated windows - and repeated GUI tests -
    working.
    """
    _cache.clear()


def prewarm(sizes=(ICON_PREWARM_SMALL, ICON_PREWARM_BASE), colors=()):
    """Rasterise the common icons ahead of time, off the UI thread.

    Rendering happens on first use otherwise, which shows up as a small pause
    the first time a page is opened. Nothing here touches Tk beyond building
    CTkImage objects, which is safe before the main loop runs.
    """
    if not available():
        return

    def _work():
        for name in _PATHS:
            for size in sizes:
                for color in colors:
                    try:
                        image(name, size, color)
                    except Exception:
                        return

    threading.Thread(target=_work, daemon=True).start()
