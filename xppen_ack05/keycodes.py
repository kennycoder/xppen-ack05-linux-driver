"""Linux input event codes and friendly key name parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02

SYN_REPORT = 0

REL_X = 0x00
REL_Y = 0x01
REL_HWHEEL = 0x06
REL_WHEEL = 0x08

# Special pseudo-actions mapped to relative wheel events instead of EV_KEY.
SCROLL_ACTIONS: dict[str, tuple[int, int]] = {
    "scroll_up": (REL_WHEEL, 1),
    "scroll_down": (REL_WHEEL, -1),
    "wheel_up": (REL_WHEEL, 1),
    "wheel_down": (REL_WHEEL, -1),
    "scroll_left": (REL_HWHEEL, -1),
    "scroll_right": (REL_HWHEEL, 1),
    "hwheel_left": (REL_HWHEEL, -1),
    "hwheel_right": (REL_HWHEEL, 1),
}

# Built-in fallback table if /usr/include/linux/input-event-codes.h is unavailable.
_FALLBACK_CODES: dict[str, int] = {
    "KEY_ESC": 1,
    "KEY_1": 2,
    "KEY_2": 3,
    "KEY_3": 4,
    "KEY_4": 5,
    "KEY_5": 6,
    "KEY_6": 7,
    "KEY_7": 8,
    "KEY_8": 9,
    "KEY_9": 10,
    "KEY_0": 11,
    "KEY_MINUS": 12,
    "KEY_EQUAL": 13,
    "KEY_BACKSPACE": 14,
    "KEY_TAB": 15,
    "KEY_Q": 16,
    "KEY_W": 17,
    "KEY_E": 18,
    "KEY_R": 19,
    "KEY_T": 20,
    "KEY_Y": 21,
    "KEY_U": 22,
    "KEY_I": 23,
    "KEY_O": 24,
    "KEY_P": 25,
    "KEY_LEFTBRACE": 26,
    "KEY_RIGHTBRACE": 27,
    "KEY_ENTER": 28,
    "KEY_LEFTCTRL": 29,
    "KEY_A": 30,
    "KEY_S": 31,
    "KEY_D": 32,
    "KEY_F": 33,
    "KEY_G": 34,
    "KEY_H": 35,
    "KEY_J": 36,
    "KEY_K": 37,
    "KEY_L": 38,
    "KEY_SEMICOLON": 39,
    "KEY_APOSTROPHE": 40,
    "KEY_GRAVE": 41,
    "KEY_LEFTSHIFT": 42,
    "KEY_BACKSLASH": 43,
    "KEY_Z": 44,
    "KEY_X": 45,
    "KEY_C": 46,
    "KEY_V": 47,
    "KEY_B": 48,
    "KEY_N": 49,
    "KEY_M": 50,
    "KEY_COMMA": 51,
    "KEY_DOT": 52,
    "KEY_SLASH": 53,
    "KEY_RIGHTSHIFT": 54,
    "KEY_KPASTERISK": 55,
    "KEY_LEFTALT": 56,
    "KEY_SPACE": 57,
    "KEY_CAPSLOCK": 58,
    "KEY_F1": 59,
    "KEY_F2": 60,
    "KEY_F3": 61,
    "KEY_F4": 62,
    "KEY_F5": 63,
    "KEY_F6": 64,
    "KEY_F7": 65,
    "KEY_F8": 66,
    "KEY_F9": 67,
    "KEY_F10": 68,
    "KEY_KP7": 71,
    "KEY_KP8": 72,
    "KEY_KP9": 73,
    "KEY_KPMINUS": 74,
    "KEY_KP4": 75,
    "KEY_KP5": 76,
    "KEY_KP6": 77,
    "KEY_KPPLUS": 78,
    "KEY_KP1": 79,
    "KEY_KP2": 80,
    "KEY_KP3": 81,
    "KEY_KP0": 82,
    "KEY_KPDOT": 83,
    "KEY_F11": 87,
    "KEY_F12": 88,
    "KEY_KPENTER": 96,
    "KEY_RIGHTCTRL": 97,
    "KEY_KPSLASH": 98,
    "KEY_SYSRQ": 99,
    "KEY_RIGHTALT": 100,
    "KEY_HOME": 102,
    "KEY_UP": 103,
    "KEY_PAGEUP": 104,
    "KEY_LEFT": 105,
    "KEY_RIGHT": 106,
    "KEY_END": 107,
    "KEY_DOWN": 108,
    "KEY_PAGEDOWN": 109,
    "KEY_INSERT": 110,
    "KEY_DELETE": 111,
    "KEY_MUTE": 113,
    "KEY_VOLUMEDOWN": 114,
    "KEY_VOLUMEUP": 115,
    "KEY_PAUSE": 119,
    "KEY_LEFTMETA": 125,
    "KEY_RIGHTMETA": 126,
    "KEY_COMPOSE": 127,
    "KEY_NEXTSONG": 163,
    "KEY_PLAYPAUSE": 164,
    "KEY_PREVIOUSSONG": 165,
    "KEY_STOPCD": 166,
    "KEY_F13": 183,
    "KEY_F14": 184,
    "KEY_F15": 185,
    "KEY_F16": 186,
    "KEY_F17": 187,
    "KEY_F18": 188,
    "KEY_F19": 189,
    "KEY_F20": 190,
    "KEY_F21": 191,
    "KEY_F22": 192,
    "KEY_F23": 193,
    "KEY_F24": 194,
    "BTN_LEFT": 0x110,
    "BTN_RIGHT": 0x111,
    "BTN_MIDDLE": 0x112,
    "BTN_SIDE": 0x113,
    "BTN_EXTRA": 0x114,
}

# Friendly short aliases -> canonical Linux KEY_* / BTN_* names.
ALIASES: dict[str, str] = {
    "ctrl": "KEY_LEFTCTRL",
    "control": "KEY_LEFTCTRL",
    "lctrl": "KEY_LEFTCTRL",
    "rctrl": "KEY_RIGHTCTRL",
    "shift": "KEY_LEFTSHIFT",
    "lshift": "KEY_LEFTSHIFT",
    "rshift": "KEY_RIGHTSHIFT",
    "alt": "KEY_LEFTALT",
    "lalt": "KEY_LEFTALT",
    "ralt": "KEY_RIGHTALT",
    "altgr": "KEY_RIGHTALT",
    "super": "KEY_LEFTMETA",
    "meta": "KEY_LEFTMETA",
    "win": "KEY_LEFTMETA",
    "cmd": "KEY_LEFTMETA",
    "lsuper": "KEY_LEFTMETA",
    "rsuper": "KEY_RIGHTMETA",
    "lmeta": "KEY_LEFTMETA",
    "rmeta": "KEY_RIGHTMETA",
    "esc": "KEY_ESC",
    "escape": "KEY_ESC",
    "enter": "KEY_ENTER",
    "return": "KEY_ENTER",
    "ret": "KEY_ENTER",
    "bs": "KEY_BACKSPACE",
    "backspace": "KEY_BACKSPACE",
    "del": "KEY_DELETE",
    "delete": "KEY_DELETE",
    "ins": "KEY_INSERT",
    "insert": "KEY_INSERT",
    "pgup": "KEY_PAGEUP",
    "pageup": "KEY_PAGEUP",
    "pgdn": "KEY_PAGEDOWN",
    "pagedown": "KEY_PAGEDOWN",
    "caps": "KEY_CAPSLOCK",
    "capslock": "KEY_CAPSLOCK",
    "prtsc": "KEY_SYSRQ",
    "printscreen": "KEY_SYSRQ",
    "plus": "KEY_EQUAL",
    "equal": "KEY_EQUAL",
    "minus": "KEY_MINUS",
    "dash": "KEY_MINUS",
    "[": "KEY_LEFTBRACE",
    "]": "KEY_RIGHTBRACE",
    "lbracket": "KEY_LEFTBRACE",
    "rbracket": "KEY_RIGHTBRACE",
    "leftbracket": "KEY_LEFTBRACE",
    "rightbracket": "KEY_RIGHTBRACE",
    ";": "KEY_SEMICOLON",
    "'": "KEY_APOSTROPHE",
    "`": "KEY_GRAVE",
    ",": "KEY_COMMA",
    ".": "KEY_DOT",
    "period": "KEY_DOT",
    "/": "KEY_SLASH",
    "\\": "KEY_BACKSLASH",
    "volup": "KEY_VOLUMEUP",
    "vol_up": "KEY_VOLUMEUP",
    "volume_up": "KEY_VOLUMEUP",
    "voldown": "KEY_VOLUMEDOWN",
    "vol_down": "KEY_VOLUMEDOWN",
    "volume_down": "KEY_VOLUMEDOWN",
    "mute": "KEY_MUTE",
    "play": "KEY_PLAYPAUSE",
    "playpause": "KEY_PLAYPAUSE",
    "play_pause": "KEY_PLAYPAUSE",
    "next": "KEY_NEXTSONG",
    "next_track": "KEY_NEXTSONG",
    "prev": "KEY_PREVIOUSSONG",
    "previous": "KEY_PREVIOUSSONG",
    "prev_track": "KEY_PREVIOUSSONG",
    "mouse_left": "BTN_LEFT",
    "mouse_right": "BTN_RIGHT",
    "mouse_middle": "BTN_MIDDLE",
    "mouse_back": "BTN_SIDE",
    "mouse_forward": "BTN_EXTRA",
}

_DEFINE_RE = re.compile(
    r"^#define\s+((?:KEY|BTN)_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|\d+)\b"
)


def _load_system_keycodes() -> dict[str, int]:
    codes = dict(_FALLBACK_CODES)
    header = Path("/usr/include/linux/input-event-codes.h")
    if not header.is_file():
        return codes
    try:
        for line in header.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = _DEFINE_RE.match(line)
            if not m:
                continue
            name, raw_val = m.groups()
            val = int(raw_val, 0)
            if 0 < val < 0x2FF:
                codes[name] = val
    except OSError:
        pass
    return codes


KEYCODES: dict[str, int] = _load_system_keycodes()


@dataclass(frozen=True)
class Action:
    """A parsed action bound to a remote button or dial direction."""

    raw: str
    kind: str  # "none", "keys", "scroll", "cmd", "dial_mode"
    keycodes: tuple[int, ...] = ()
    rel_axis: int = 0
    rel_value: int = 0
    command: str = ""
    mode_target: str = ""  # "next", "prev", or a specific mode name


def resolve_keycode(token: str) -> int:
    """Resolve a single key token like 'ctrl', 'z', 'f12', or 'KEY_SPACE'."""
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("Empty key token")

    lower = cleaned.lower()
    if lower in ALIASES:
        canonical = ALIASES[lower]
        if canonical in KEYCODES:
            return KEYCODES[canonical]

    upper = cleaned.upper()
    if upper in KEYCODES:
        return KEYCODES[upper]

    prefixed = f"KEY_{upper}"
    if prefixed in KEYCODES:
        return KEYCODES[prefixed]

    btn_prefixed = f"BTN_{upper}"
    if btn_prefixed in KEYCODES:
        return KEYCODES[btn_prefixed]

    raise ValueError(f"Unknown key name: {token!r}")


def parse_action(spec: str | None) -> Action:
    """Parse a mapping string such as 'ctrl+z', 'scroll_up', 'cmd:...', or 'dial_mode_next'."""
    if spec is None:
        return Action(raw="", kind="none")

    text = spec.strip()
    lower = text.lower()
    if not text or lower in {"none", "disabled", "noop"}:
        return Action(raw=text, kind="none")

    if lower.startswith("cmd:"):
        command = text[4:].strip()
        if not command:
            raise ValueError("Empty command after 'cmd:'")
        return Action(raw=text, kind="cmd", command=command)

    if lower in {"dial_mode_next", "mode_next"}:
        return Action(raw=text, kind="dial_mode", mode_target="next")

    if lower in {"dial_mode_prev", "mode_prev"}:
        return Action(raw=text, kind="dial_mode", mode_target="prev")

    if lower.startswith("dial_mode:"):
        target = text.split(":", 1)[1].strip()
        if not target:
            raise ValueError("Empty mode name after 'dial_mode:'")
        return Action(raw=text, kind="dial_mode", mode_target=target)

    if lower in SCROLL_ACTIONS:
        axis, value = SCROLL_ACTIONS[lower]
        return Action(raw=text, kind="scroll", rel_axis=axis, rel_value=value)

    parts = [p.strip() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Invalid key mapping: {spec!r}")

    # Allow modifier+scroll chords such as "ctrl+scroll_up".
    if parts[-1].lower() in SCROLL_ACTIONS:
        axis, value = SCROLL_ACTIONS[parts[-1].lower()]
        mods = tuple(resolve_keycode(p) for p in parts[:-1])
        return Action(
            raw=text,
            kind="scroll",
            keycodes=mods,
            rel_axis=axis,
            rel_value=value,
        )

    codes = tuple(resolve_keycode(p) for p in parts)
    return Action(raw=text, kind="keys", keycodes=codes)


def list_friendly_keys() -> list[str]:
    """Return sorted friendly aliases and standard key names."""
    names = set(ALIASES.keys()) | set(SCROLL_ACTIONS.keys())
    for k in KEYCODES:
        if k.startswith("KEY_"):
            names.add(k[4:].lower())
        else:
            names.add(k.lower())
    return sorted(names)
