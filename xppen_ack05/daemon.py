"""Main daemon loop and CLI for the XPPen ACK05 Linux driver."""

from __future__ import annotations

import argparse
import errno
import logging
import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import DEFAULT_CONFIG_PATH, Config, DialMode, load_config
from .keycodes import Action, list_friendly_keys
from .osd import show_osd
from .protocol import (
    Ack05Device,
    BatteryPacket,
    InputPacket,
    StatusPacket,
    UnknownPacket,
    decode_pressed_buttons,
    find_ack05_hidraw,
    parse_report,
)
from .uinput import VirtualInputDevice

log = logging.getLogger("xppen-ack05")


def _spawn_command(command: str) -> None:
    """Launch a configured shell command asynchronously without blocking the event loop."""
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    repo_dir = str(Path(__file__).resolve().parent.parent)
    extra_paths = [p for p in (local_bin, repo_dir) if p not in env.get("PATH", "").split(":")]
    if extra_paths:
        env["PATH"] = ":".join(extra_paths + ([env["PATH"]] if env.get("PATH") else []))

    try:
        subprocess.Popen(
            command,
            shell=True,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.error("Failed to execute command %r: %s", command, exc)


class ButtonTracker:
    """Translates consecutive button bitmasks into press/release edge events."""

    def __init__(
        self,
        emitter: VirtualInputDevice,
        config: Config,
        command_runner: Callable[[str], None] = _spawn_command,
        osd_notifier: Callable[[str, str], None] = show_osd,
    ) -> None:
        self._emitter = emitter
        self._config = config
        self._command_runner = command_runner
        self._osd_notifier = osd_notifier
        self._pressed: set[str] = set()
        self._dial_mode_index: int = 0

    @property
    def active_dial_mode(self) -> DialMode | None:
        if not self._config.dial_modes:
            return None
        return self._config.dial_modes[self._dial_mode_index % len(self._config.dial_modes)]

    def update_config(self, config: Config) -> None:
        prev_mode = self.active_dial_mode
        self.reset()
        self._config = config
        if prev_mode and config.dial_modes:
            for idx, mode in enumerate(config.dial_modes):
                if mode.name.lower() == prev_mode.name.lower():
                    self._dial_mode_index = idx
                    return
        self._dial_mode_index = 0

    def _switch_dial_mode(self, target: str) -> None:
        modes = self._config.dial_modes
        if not modes:
            log.warning("Dial mode switch requested (%s), but no [[dial_modes]] are configured.", target)
            return

        if target == "next":
            self._dial_mode_index = (self._dial_mode_index + 1) % len(modes)
        elif target == "prev":
            self._dial_mode_index = (self._dial_mode_index - 1) % len(modes)
        else:
            for idx, mode in enumerate(modes):
                if mode.name.lower() == target.lower():
                    self._dial_mode_index = idx
                    break
            else:
                log.warning("Unknown dial mode target: %r", target)
                return

        mode = modes[self._dial_mode_index]
        log.info("Dial mode -> %s (%d/%d)", mode.name, self._dial_mode_index + 1, len(modes))
        if self._config.osd:
            self._osd_notifier(f"Dial: {mode.name}", mode.icon)

    def _dispatch_special(self, action: Action) -> bool:
        if action.kind == "cmd":
            self._command_runner(action.command)
            return True
        if action.kind == "dial_mode":
            self._switch_dial_mode(action.mode_target)
            return True
        return False

    def _resolve_dial_action(self, direction: str) -> Action | None:
        mode = self.active_dial_mode
        if mode is not None:
            return mode.cw if direction == "dial_cw" else mode.ccw
        return self._config.mappings.get(direction)

    def handle_packet(self, packet: InputPacket, debug: bool = False) -> None:
        now_pressed = decode_pressed_buttons(packet.button_mask, self._config.button_bits)

        newly_released = self._pressed - now_pressed
        newly_pressed = now_pressed - self._pressed

        for btn in sorted(newly_released):
            action = self._config.mappings.get(btn)
            if debug:
                log.info("RELEASE %-11s -> %s", btn, action.raw if action else "none")
            if action and action.kind == "keys":
                self._emitter.release_action(action)

        for btn in sorted(newly_pressed):
            action = self._config.mappings.get(btn)
            if debug:
                log.info("PRESS   %-11s -> %s", btn, action.raw if action else "none")
            if action:
                if not self._dispatch_special(action):
                    self._emitter.press_action(action)

        self._pressed = now_pressed

        if packet.dial is not None:
            action = self._resolve_dial_action(packet.dial)
            if debug:
                mode_label = f" [{self.active_dial_mode.name}]" if self.active_dial_mode else ""
                log.info("DIAL    %-11s%s -> %s", packet.dial, mode_label, action.raw if action else "none")
            if action:
                if not self._dispatch_special(action):
                    self._emitter.tap_action(action, hold_seconds=self._config.dial_tap_ms / 1000.0)

    def reset(self) -> None:
        self._pressed.clear()
        self._emitter.release_all()


class _NullEmitter:
    """No-op emitter used by --debug --dry-run to inspect raw hardware packets safely."""

    def press_action(self, *_: object, **__: object) -> None:
        pass

    def release_action(self, *_: object, **__: object) -> None:
        pass

    def tap_action(self, *_: object, **__: object) -> None:
        pass

    def release_all(self) -> None:
        pass


def resolve_config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    local_default = Path(__file__).resolve().parent.parent / "config.toml"
    if local_default.is_file():
        return local_default
    return DEFAULT_CONFIG_PATH


def run_check(config_path: Path) -> int:
    ok = True
    print(f"Config path: {config_path}")
    if not config_path.is_file():
        print("  [FAIL] Config file does not exist.")
        ok = False
    else:
        try:
            cfg = load_config(config_path)
            active = {k: v.raw for k, v in cfg.mappings.items() if v.kind != "none"}
            mode_info = (
                f", {len(cfg.dial_modes)} dial modes: "
                + " / ".join(m.name for m in cfg.dial_modes)
                if cfg.dial_modes
                else ""
            )
            print(f"  [OK]   Loaded config ({len(active)} active mappings{mode_info}).")
        except Exception as exc:
            print(f"  [FAIL] Invalid config: {exc}")
            ok = False

    hidraw = find_ack05_hidraw()
    if hidraw is None:
        print("Device:  [FAIL] XPPen ACK05 (28bd:0202) vendor hidraw interface not found.")
        ok = False
    else:
        readable = os.access(hidraw, os.R_OK | os.W_OK)
        status = "OK" if readable else "FAIL (no read/write permission — install udev rule)"
        print(f"Device:  [{status}] {hidraw}")
        if not readable:
            ok = False

    uinput_path = Path("/dev/uinput")
    if not uinput_path.exists():
        print("uinput:  [FAIL] /dev/uinput does not exist (try: sudo modprobe uinput).")
        ok = False
    elif not os.access(uinput_path, os.W_OK):
        print("uinput:  [FAIL] /dev/uinput is not writable by current user.")
        ok = False
    else:
        print("uinput:  [OK]   /dev/uinput is writable.")

    return 0 if ok else 1


def run_daemon(
    config_path: Path,
    device_override: Path | None = None,
    debug: bool = False,
    dry_run: bool = False,
) -> int:
    config = load_config(config_path)
    config_mtime = config_path.stat().st_mtime
    stop_requested = False
    reload_requested = False

    def _on_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        log.info("Received signal %s, shutting down...", signum)
        stop_requested = True

    def _on_reload(_signum: int, _frame: object) -> None:
        nonlocal reload_requested
        reload_requested = True

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGHUP, _on_reload)

    emitter: VirtualInputDevice | _NullEmitter
    if dry_run:
        emitter = _NullEmitter()
        log.info("Dry-run mode enabled: NOT creating /dev/uinput device or emitting keys.")
        tracker = ButtonTracker(
            emitter,  # type: ignore[arg-type]
            config,
            command_runner=lambda cmd: log.info("[dry-run] Would run command: %s", cmd),
            osd_notifier=lambda text, icon: log.info("[dry-run] OSD (%s): %s", icon, text),
        )
    else:
        emitter = VirtualInputDevice()
        emitter.open()
        log.info("Created virtual input device via /dev/uinput.")
        tracker = ButtonTracker(emitter, config)

    def _maybe_reload_config() -> None:
        nonlocal config, config_mtime, reload_requested
        should_reload = reload_requested
        reload_requested = False
        if not should_reload and config.hot_reload:
            try:
                mtime = config_path.stat().st_mtime
                if mtime != config_mtime:
                    should_reload = True
                    config_mtime = mtime
            except OSError:
                return
        if should_reload:
            try:
                new_cfg = load_config(config_path)
                config = new_cfg
                config_mtime = config_path.stat().st_mtime
                tracker.update_config(config)
                log.info("Reloaded configuration from %s", config_path)
            except Exception as exc:
                log.error("Failed to reload config %s: %s (keeping previous config)", config_path, exc)

    try:
        waiting_logged = False
        while not stop_requested:
            _maybe_reload_config()

            dev_path = device_override or find_ack05_hidraw()
            if dev_path is None:
                if not waiting_logged:
                    log.info("Waiting for XPPen ACK05 (28bd:0202)...")
                    waiting_logged = True
                time.sleep(1.0)
                continue

            waiting_logged = False
            try:
                with Ack05Device(dev_path) as dev:
                    dev.send_init()
                    log.info("Connected to %s and enabled vendor bitmask mode.", dev_path)
                    remote_connected = True
                    last_init_at = time.monotonic()

                    def _reinit_if_allowed(reason: str) -> None:
                        nonlocal last_init_at
                        now = time.monotonic()
                        if now - last_init_at < 2.0:
                            return
                        try:
                            dev.send_init()
                            last_init_at = now
                            log.info("Re-sent vendor mode init packet (%s).", reason)
                        except OSError as exc:
                            log.warning("Failed to re-init (%s): %s", reason, exc)

                    while not stop_requested:
                        _maybe_reload_config()
                        ready, _, _ = select.select([dev.fd], [], [], 0.5)
                        if not ready:
                            continue

                        raw = dev.read_report()
                        if raw is None:
                            continue

                        if debug:
                            log.info("RAW: %s", raw.hex(" "))

                        pkt = parse_report(raw)
                        if isinstance(pkt, InputPacket):
                            remote_connected = True
                            if debug:
                                bits = [i for i in range(16) if pkt.button_mask & (1 << i)]
                                log.info(
                                    "PKT: mask=0x%04x bits=%s dial=%s",
                                    pkt.button_mask,
                                    bits,
                                    pkt.dial,
                                )
                            tracker.handle_packet(pkt, debug=debug)
                        elif isinstance(pkt, StatusPacket):
                            if pkt.connected:
                                log.info(
                                    "Remote status: connected/awake (0x%02x 0x%02x)",
                                    pkt.code,
                                    pkt.value,
                                )
                                remote_connected = True
                                _reinit_if_allowed("remote woke/reconnected")
                            else:
                                if remote_connected or debug:
                                    log.info(
                                        "Remote status: disconnected/sleep (0x%02x 0x%02x)",
                                        pkt.code,
                                        pkt.value,
                                    )
                                remote_connected = False
                                tracker.reset()
                        elif isinstance(pkt, BatteryPacket):
                            if not remote_connected:
                                remote_connected = True
                                _reinit_if_allowed("battery packet after sleep")
                            if debug:
                                log.info(
                                    "Battery: %s%% (charging=%s)",
                                    pkt.percent,
                                    pkt.charging,
                                )
                        elif isinstance(pkt, UnknownPacket) and debug:
                            log.info("Unknown report: %s", pkt.raw.hex(" "))

            except PermissionError as exc:
                log.error(
                    "Permission denied opening %s (%s). Install 70-xppen-ack05.rules and reload udev.",
                    dev_path,
                    exc,
                )
                time.sleep(3.0)
            except OSError as exc:
                tracker.reset()
                if exc.errno in (errno.ENODEV, errno.EIO, errno.ESHUTDOWN):
                    log.warning("Device %s disconnected (%s). Waiting to reconnect...", dev_path, exc)
                else:
                    log.error("Device I/O error on %s: %s. Retrying...", dev_path, exc)
                time.sleep(1.0)
    finally:
        tracker.reset()
        if isinstance(emitter, VirtualInputDevice):
            emitter.close()
        log.info("Stopped.")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xppen-ack05",
        description="Lightweight Linux hidraw + uinput remapper for the XPPen ACK05 Shortcut Remote.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help=f"Path to TOML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-d",
        "--device",
        help="Explicit /dev/hidrawX path (auto-detected by default)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log raw HID packets, bit indices, and mapped events",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read hardware packets without creating /dev/uinput or emitting keys (implies --debug)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config file and check device/uinput permissions, then exit",
    )
    parser.add_argument(
        "--list-keys",
        action="store_true",
        help="List all recognized key and scroll action names, then exit",
    )

    args = parser.parse_args(argv)

    if args.list_keys:
        print("\n".join(list_friendly_keys()))
        return 0

    debug = args.debug or args.dry_run
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = resolve_config_path(args.config)

    if args.check:
        return run_check(config_path)

    device_override = Path(args.device) if args.device else None
    try:
        return run_daemon(
            config_path=config_path,
            device_override=device_override,
            debug=debug,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.error("Fatal error: %s", exc)
        return 1
