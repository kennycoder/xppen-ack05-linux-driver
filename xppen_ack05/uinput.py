"""Pure-Python /dev/uinput virtual keyboard & scroll emitter."""

from __future__ import annotations

import fcntl
import os
import struct
import time
from collections import Counter
from typing import Iterable

from .keycodes import (
    EV_KEY,
    EV_REL,
    EV_SYN,
    KEYCODES,
    REL_HWHEEL,
    REL_WHEEL,
    REL_X,
    REL_Y,
    SYN_REPORT,
    Action,
)

# Linux ioctl encoding helpers (matches <asm-generic/ioctl.h> on x86_64/arm64)
_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS


def _ioc(direction: int, ioc_type: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ioc_type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _io(ioc_type: int, nr: int) -> int:
    return _ioc(_IOC_NONE, ioc_type, nr, 0)


def _iow(ioc_type: int, nr: int, size: int) -> int:
    return _ioc(_IOC_WRITE, ioc_type, nr, size)


_UINPUT_TYPE = ord("U")
_UINPUT_MAX_NAME_SIZE = 80
_BUS_VIRTUAL = 0x06

# struct uinput_setup { struct input_id id (4*u16); char name[80]; u32 ff_effects_max; }
_UINPUT_SETUP_FMT = f"=HHHH{_UINPUT_MAX_NAME_SIZE}sI"
_UINPUT_SETUP_SIZE = struct.calcsize(_UINPUT_SETUP_FMT)  # 92 bytes

# struct input_event { struct timeval time (2*long); u16 type; u16 code; s32 value; }
_INPUT_EVENT_FMT = "llHHi"

UI_DEV_CREATE = _io(_UINPUT_TYPE, 1)
UI_DEV_DESTROY = _io(_UINPUT_TYPE, 2)
UI_DEV_SETUP = _iow(_UINPUT_TYPE, 3, _UINPUT_SETUP_SIZE)
UI_SET_EVBIT = _iow(_UINPUT_TYPE, 100, 4)
UI_SET_KEYBIT = _iow(_UINPUT_TYPE, 101, 4)
UI_SET_RELBIT = _iow(_UINPUT_TYPE, 102, 4)


# Mouse buttons safe to register alongside keyboard keys so libinput treats the
# virtual device cleanly as a keyboard + mouse/scroll wheel (never a joystick/tablet).
_SAFE_MOUSE_BUTTONS = {0x110, 0x111, 0x112, 0x113, 0x114}
_EXCLUDED_KEYCODES = {247}  # KEY_RFKILL (avoid attaching kernel rfkill handler)


def _default_uinput_keycodes() -> set[int]:
    """Return standard keyboard/media KEY_* codes (1..248) + basic mouse buttons.

    Explicitly excludes BTN_DPAD_* / BTN_TRIGGER_HAPPY* / BTN_DIGI* so the kernel
    and udev never classify the virtual keyboard as a joystick or drawing tablet.
    """
    codes = {
        code
        for name, code in KEYCODES.items()
        if name.startswith("KEY_") and 0 < code <= 248 and code not in _EXCLUDED_KEYCODES
    }
    codes.update(_SAFE_MOUSE_BUTTONS)
    return codes


class VirtualInputDevice:
    """Virtual keyboard + scroll wheel backed by /dev/uinput."""

    def __init__(
        self,
        name: str = "XPPen ACK05 Virtual Remote",
        vendor: int = 0x0000,
        product: int = 0x0000,
        version: int = 1,
        enabled_keycodes: Iterable[int] | None = None,
    ) -> None:
        self._fd: int | None = None
        self._held_counts: Counter[int] = Counter()
        self._name = name
        self._vendor = vendor
        self._product = product
        self._version = version
        self._enabled_keycodes = (
            set(enabled_keycodes)
            if enabled_keycodes is not None
            else _default_uinput_keycodes()
        )

    def open(self) -> None:
        if self._fd is not None:
            return

        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)

            for code in sorted(self._enabled_keycodes):
                if 0 < code < 0x2FF:
                    fcntl.ioctl(fd, UI_SET_KEYBIT, code)

            # Register REL_X/REL_Y alongside REL_WHEEL/REL_HWHEEL so udev input_id
            # sets ID_INPUT_MOUSE=1 and libinput delivers scroll wheel events.
            fcntl.ioctl(fd, UI_SET_RELBIT, REL_X)
            fcntl.ioctl(fd, UI_SET_RELBIT, REL_Y)
            fcntl.ioctl(fd, UI_SET_RELBIT, REL_WHEEL)
            fcntl.ioctl(fd, UI_SET_RELBIT, REL_HWHEEL)

            encoded_name = self._name.encode("utf-8")[: _UINPUT_MAX_NAME_SIZE - 1]
            setup = struct.pack(
                _UINPUT_SETUP_FMT,
                _BUS_VIRTUAL,
                self._vendor,
                self._product,
                self._version,
                encoded_name,
                0,
            )
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except Exception:
            os.close(fd)
            raise

        self._fd = fd
        # Give the compositor/libinput a brief moment to register the new virtual device.
        time.sleep(0.15)

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            self.release_all()
        except OSError:
            pass
        try:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        try:
            os.close(self._fd)
        finally:
            self._fd = None

    def __enter__(self) -> VirtualInputDevice:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _emit_raw(self, ev_type: int, code: int, value: int) -> None:
        if self._fd is None:
            raise RuntimeError("VirtualInputDevice is not open")
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        os.write(self._fd, struct.pack(_INPUT_EVENT_FMT, sec, usec, ev_type, code, value))

    def _sync(self) -> None:
        self._emit_raw(EV_SYN, SYN_REPORT, 0)

    def _emit_scroll(self, action: Action) -> None:
        if action.keycodes:
            for code in action.keycodes:
                if self._held_counts[code] == 0:
                    self._emit_raw(EV_KEY, code, 1)
                    self._sync()
                self._held_counts[code] += 1
        self._emit_raw(EV_REL, action.rel_axis, action.rel_value)
        self._sync()
        if action.keycodes:
            for code in reversed(action.keycodes):
                if self._held_counts[code] > 0:
                    self._held_counts[code] -= 1
                    if self._held_counts[code] == 0:
                        del self._held_counts[code]
                        self._emit_raw(EV_KEY, code, 0)
                        self._sync()

    def press_action(self, action: Action) -> None:
        """Press (and hold) a key or chord, or emit a scroll tick."""
        if action.kind == "none":
            return
        if action.kind == "scroll":
            self._emit_scroll(action)
            return
        if action.kind == "keys":
            for code in action.keycodes:
                if self._held_counts[code] == 0:
                    self._emit_raw(EV_KEY, code, 1)
                    self._sync()
                self._held_counts[code] += 1

    def release_action(self, action: Action) -> None:
        """Release a previously pressed key or chord in reverse order."""
        if action.kind != "keys":
            return
        for code in reversed(action.keycodes):
            if self._held_counts[code] > 0:
                self._held_counts[code] -= 1
                if self._held_counts[code] == 0:
                    del self._held_counts[code]
                    self._emit_raw(EV_KEY, code, 0)
                    self._sync()

    def tap_action(self, action: Action, hold_seconds: float = 0.012) -> None:
        """Emit a momentary press + release (used for dial rotation ticks)."""
        if action.kind == "none":
            return
        if action.kind == "scroll":
            self._emit_scroll(action)
            return
        if action.kind == "keys":
            self.press_action(action)
            if hold_seconds > 0:
                time.sleep(hold_seconds)
            self.release_action(action)

    def release_all(self) -> None:
        """Release every currently held key to avoid stuck modifiers."""
        if not self._held_counts:
            return
        for code in list(self._held_counts.keys()):
            self._emit_raw(EV_KEY, code, 0)
        self._held_counts.clear()
        self._sync()
