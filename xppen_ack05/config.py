"""TOML configuration loader and validator for xppen-ack05."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .keycodes import Action, parse_action
from .protocol import ALL_CONTROLS, DEFAULT_BUTTON_BITS

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "xppen-ack05" / "config.toml"


@dataclass(frozen=True)
class DialMode:
    """A selectable dial mode with its own clockwise/counter-clockwise actions."""

    name: str
    cw: Action
    ccw: Action
    icon: str = "input-dialpad"


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    mappings: dict[str, Action]
    button_bits: dict[int, str]
    dial_modes: tuple[DialMode, ...] = ()
    dial_tap_ms: int = 12
    hot_reload: bool = True
    osd: bool = True

    def all_keycodes(self) -> set[int]:
        codes: set[int] = set()
        for action in self.mappings.values():
            codes.update(action.keycodes)
        for mode in self.dial_modes:
            codes.update(mode.cw.keycodes)
            codes.update(mode.ccw.keycodes)
        return codes


def load_config(path: Path) -> Config:
    """Load and validate a TOML configuration file."""
    raw_text = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw_text)

    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a TOML table")

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("[settings] must be a TOML table")

    dial_tap_ms = int(settings.get("dial_tap_ms", 12))
    if dial_tap_ms < 0 or dial_tap_ms > 500:
        raise ValueError("settings.dial_tap_ms must be between 0 and 500")

    hot_reload = bool(settings.get("hot_reload", True))
    osd = bool(settings.get("osd", True))

    # Optional bit-index overrides in [hardware.button_bits] if a firmware revision differs.
    button_bits = dict(DEFAULT_BUTTON_BITS)
    hardware = data.get("hardware", {})
    if hardware:
        if not isinstance(hardware, dict):
            raise ValueError("[hardware] must be a TOML table")
        raw_bits = hardware.get("button_bits", {})
        if raw_bits:
            if not isinstance(raw_bits, dict):
                raise ValueError("[hardware.button_bits] must be a TOML table")
            custom_bits: dict[int, str] = {}
            for name, bit_idx in raw_bits.items():
                if name not in ALL_CONTROLS or name in ("dial_cw", "dial_ccw"):
                    raise ValueError(f"Unknown button name in [hardware.button_bits]: {name!r}")
                bit = int(bit_idx)
                if bit < 0 or bit > 15:
                    raise ValueError(f"Bit index for {name!r} must be 0..15, got {bit}")
                custom_bits[bit] = name
            button_bits = custom_bits

    raw_keys = data.get("keys", {})
    if not isinstance(raw_keys, dict):
        raise ValueError("[keys] must be a TOML table")

    mappings: dict[str, Action] = {ctrl: parse_action("none") for ctrl in ALL_CONTROLS}
    for control, spec in raw_keys.items():
        if control not in ALL_CONTROLS:
            valid = ", ".join(ALL_CONTROLS)
            raise ValueError(f"Unknown control {control!r} in [keys]. Valid: {valid}")
        if not isinstance(spec, str):
            raise ValueError(f"Mapping for {control!r} must be a string, got {type(spec).__name__}")
        mappings[control] = parse_action(spec)

    # Optional [[dial_modes]] list for multi-mode dial switching.
    dial_modes: list[DialMode] = []
    raw_modes = data.get("dial_modes", [])
    if raw_modes:
        if not isinstance(raw_modes, list):
            raise ValueError("[[dial_modes]] must be a TOML array of tables")
        for idx, entry in enumerate(raw_modes):
            if not isinstance(entry, dict):
                raise ValueError(f"dial_modes[{idx}] must be a table")
            name = str(entry.get("name", f"Mode {idx + 1}")).strip()
            if not name:
                raise ValueError(f"dial_modes[{idx}].name must not be empty")
            cw_raw = entry.get("cw", "none")
            ccw_raw = entry.get("ccw", "none")
            if not isinstance(cw_raw, str) or not isinstance(ccw_raw, str):
                raise ValueError(f"dial_modes[{idx}] cw/ccw must be strings")
            icon = str(entry.get("icon", "input-dialpad")).strip() or "input-dialpad"
            dial_modes.append(
                DialMode(
                    name=name,
                    cw=parse_action(cw_raw),
                    ccw=parse_action(ccw_raw),
                    icon=icon,
                )
            )

    return Config(
        mappings=mappings,
        button_bits=button_bits,
        dial_modes=tuple(dial_modes),
        dial_tap_ms=dial_tap_ms,
        hot_reload=hot_reload,
        osd=osd,
    )
