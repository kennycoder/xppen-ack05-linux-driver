"""XPPen ACK05 (28bd:0202) HID raw discovery and vendor protocol parser."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VENDOR_ID = 0x28BD
PRODUCT_ID = 0x0202

# Vendor-defined HID Usage Page 0xFF0A (Interface 2 on the ACK05 dongle/cable).
VENDOR_DESCRIPTOR_PREFIX = b"\x06\x0a\xff"

# Output report (Report ID 0x02 + 63 bytes = 64 bytes) that switches the ACK05
# from factory keyboard emulation into raw vendor bitmask mode.
INIT_REPORT = bytes([0x02, 0xB0, 0x04]) + bytes(61)

REPORT_ID_VENDOR = 0x02
SUBTYPE_INPUT = 0xF0
SUBTYPE_BATTERY = 0xF2
SUBTYPE_STATUS = 0xF8

# Default ACK05 physical button bit positions in the 16-bit mask (data[2] | data[3]<<8).
DEFAULT_BUTTON_BITS: dict[int, str] = {
    0: "k1",
    1: "k2",
    2: "k3",
    3: "k4",
    4: "k5",
    5: "k6",
    6: "k7",
    7: "k8",
    8: "k9",
    9: "k10",
    10: "dial_button",
}

ALL_CONTROLS: tuple[str, ...] = (
    "k1",
    "k2",
    "k3",
    "k4",
    "k5",
    "k6",
    "k7",
    "k8",
    "k9",
    "k10",
    "dial_button",
    "dial_cw",
    "dial_ccw",
)


@dataclass(frozen=True)
class InputPacket:
    """Decoded 0x02 0xF0 button + dial packet."""

    button_mask: int
    dial: str | None  # "dial_cw", "dial_ccw", or None
    raw: bytes


@dataclass(frozen=True)
class BatteryPacket:
    """Decoded 0x02 0xF2 battery report."""

    percent: int | None
    charging: bool | None
    raw: bytes


@dataclass(frozen=True)
class StatusPacket:
    """Decoded 0x02 0xF8 wireless status packet.

    On the XPPen/Ugee 2.4 GHz dongle:
      - 02 f8 02 01 ... : wireless remote connected / woke up
      - 02 f8 04 01 ... : wireless remote disconnected / went to sleep
    """

    connected: bool
    code: int
    value: int
    raw: bytes


@dataclass(frozen=True)
class UnknownPacket:
    """Any unrecognized vendor packet."""

    raw: bytes


ParsedPacket = InputPacket | BatteryPacket | StatusPacket | UnknownPacket


def find_ack05_hidraw(
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
    sysfs_root: Path = Path("/sys/class/hidraw"),
) -> Path | None:
    """Find the /dev/hidraw* path for the ACK05 vendor interface (0xFF0A)."""
    if not sysfs_root.is_dir():
        return None

    target_hid_id = f":{vendor_id:08X}:{product_id:08X}"
    fallback: Path | None = None

    for entry in sorted(sysfs_root.iterdir()):
        uevent_path = entry / "device" / "uevent"
        if not uevent_path.is_file():
            continue
        try:
            uevent = uevent_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        matched_id = False
        is_input2 = False
        for line in uevent.splitlines():
            if line.startswith("HID_ID=") and target_hid_id in line.upper():
                matched_id = True
            elif line.startswith("HID_PHYS=") and line.endswith("/input2"):
                is_input2 = True

        if not matched_id:
            continue

        dev_path = Path("/dev") / entry.name
        desc_path = entry / "device" / "report_descriptor"
        try:
            desc = desc_path.read_bytes()
            if desc.startswith(VENDOR_DESCRIPTOR_PREFIX):
                return dev_path
        except OSError:
            pass

        if is_input2:
            fallback = dev_path

    return fallback


def parse_report(data: bytes) -> ParsedPacket:
    """Parse a raw HID input report from the ACK05 vendor interface."""
    if len(data) < 2 or data[0] != REPORT_ID_VENDOR:
        return UnknownPacket(raw=data)

    subtype = data[1]

    if subtype == SUBTYPE_INPUT and len(data) >= 8:
        mask = data[2] | (data[3] << 8)
        dial_byte = data[7]
        if dial_byte == 0x01:
            dial = "dial_cw"
        elif dial_byte in (0x02, 0xFF):
            dial = "dial_ccw"
        else:
            dial = None
        return InputPacket(button_mask=mask, dial=dial, raw=data)

    if subtype == SUBTYPE_BATTERY and len(data) >= 4:
        # Common ACK05 battery layout: data[3] is percentage (0-100), data[2] state.
        percent = data[3] if 0 <= data[3] <= 100 else None
        charging = bool(data[2] & 0x01) if len(data) >= 3 else None
        return BatteryPacket(percent=percent, charging=charging, raw=data)

    if subtype == SUBTYPE_STATUS and len(data) >= 4:
        code = data[2]
        value = data[3]
        if code == 0x02:
            connected = value != 0x00
        elif code == 0x04:
            # 02 f8 04 01 indicates wireless link lost / remote entered sleep.
            connected = False
        else:
            return UnknownPacket(raw=data)
        return StatusPacket(connected=connected, code=code, value=value, raw=data)

    return UnknownPacket(raw=data)


def decode_pressed_buttons(
    mask: int, button_bits: dict[int, str] | None = None
) -> set[str]:
    """Convert a button bitmask into a set of logical button names."""
    mapping = button_bits if button_bits is not None else DEFAULT_BUTTON_BITS
    return {name for bit, name in mapping.items() if mask & (1 << bit)}


class Ack05Device:
    """Wrapper around the ACK05 vendor /dev/hidraw* file descriptor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise RuntimeError("Ack05Device is not open")
        return self._fd

    def open(self) -> None:
        if self._fd is not None:
            return
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_NONBLOCK)

    def send_init(self) -> None:
        """Switch the ACK05 into raw vendor bitmask mode."""
        written = os.write(self.fd, INIT_REPORT)
        if written != len(INIT_REPORT):
            raise OSError(f"Short write sending init report: {written}/{len(INIT_REPORT)}")

    def read_report(self) -> bytes | None:
        """Read a single raw HID report, returning None if no data is ready."""
        try:
            data = os.read(self.fd, 64)
        except BlockingIOError:
            return None
        if not data:
            return None
        return data

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def __enter__(self) -> Ack05Device:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
