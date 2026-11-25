"""PipeWire / PulseAudio (pactl) default sink & source switcher."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .osd import show_osd


@dataclass(frozen=True)
class AudioDevice:
    name: str
    description: str
    is_monitor: bool = False


def _run_pactl(*args: str) -> subprocess.CompletedProcess[str]:
    pactl = shutil.which("pactl")
    if not pactl:
        raise RuntimeError("pactl not found in PATH")
    return subprocess.run(
        [pactl, *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _parse_pactl_text(text: str, is_source: bool) -> list[AudioDevice]:
    """Fallback parser for plain `pactl list sinks/sources` output."""
    devices: list[AudioDevice] = []
    name = ""
    desc = ""
    monitor_of = False

    def _flush() -> None:
        nonlocal name, desc, monitor_of
        if name:
            is_mon = is_source and (monitor_of or name.endswith(".monitor"))
            devices.append(AudioDevice(name=name, description=desc or name, is_monitor=is_mon))
        name = ""
        desc = ""
        monitor_of = False

    for line in text.splitlines():
        if re.match(r"^(Sink|Source)\s+#\d+", line):
            _flush()
            continue
        stripped = line.strip()
        if stripped.startswith("Name:"):
            name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Description:"):
            desc = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Monitor of Sink:"):
            val = stripped.split(":", 1)[1].strip().lower()
            if val and val != "n/a":
                monitor_of = True
    _flush()
    return devices


def list_devices(kind: str) -> list[AudioDevice]:
    """Return available PulseAudio/PipeWire 'sinks' or 'sources'."""
    if kind not in ("sinks", "sources"):
        raise ValueError(f"Unsupported device kind: {kind}")

    is_source = kind == "sources"
    proc = _run_pactl("-f", "json", "list", kind)
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
            devices: list[AudioDevice] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                desc = str(item.get("description") or name).strip()
                monitor_of = item.get("monitor_of")
                is_mon = is_source and (bool(monitor_of) or name.endswith(".monitor"))
                devices.append(AudioDevice(name=name, description=desc, is_monitor=is_mon))
            return devices
        except json.JSONDecodeError:
            pass

    proc = _run_pactl("list", kind)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"pactl list {kind} failed")
    return _parse_pactl_text(proc.stdout, is_source=is_source)


def match_device(
    devices: list[AudioDevice],
    query: str,
    exclude_monitors: bool = True,
) -> AudioDevice | None:
    """Match a device by exact description, substring, or all whitespace tokens."""
    candidates = [d for d in devices if not (exclude_monitors and d.is_monitor)]
    q = " ".join(query.lower().split())
    if not q:
        return None

    # 1. Exact description or name match
    for dev in candidates:
        if dev.description.lower() == q or dev.name.lower() == q:
            return dev

    # 2. Substring match in description
    for dev in candidates:
        if q in dev.description.lower():
            return dev

    # 3. Substring match in internal PipeWire/Pulse name
    for dev in candidates:
        if q in dev.name.lower():
            return dev

    # 4. All tokens present in combined description + name
    tokens = q.split()
    for dev in candidates:
        haystack = f"{dev.description} {dev.name}".lower()
        if all(tok in haystack for tok in tokens):
            return dev

    return None


def _move_active_streams(list_kind: str, move_cmd: str, target_name: str) -> None:
    proc = _run_pactl("list", "short", list_kind)
    if proc.returncode != 0:
        return
    for line in proc.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        stream_id = parts[0]
        if stream_id.isdigit():
            _run_pactl(move_cmd, stream_id, target_name)


def switch_audio(
    sink_query: str | None = None,
    source_query: str | None = None,
    label: str | None = None,
    icon: str = "audio-card",
    osd: bool = True,
) -> int:
    errors: list[str] = []
    switched: list[str] = []

    if sink_query:
        try:
            sinks = list_devices("sinks")
            sink = match_device(sinks, sink_query, exclude_monitors=False)
            if sink is None:
                errors.append(f"Sink not found: {sink_query}")
            else:
                res = _run_pactl("set-default-sink", sink.name)
                if res.returncode != 0:
                    errors.append(res.stderr.strip() or f"Failed to set sink {sink.name}")
                else:
                    _move_active_streams("sink-inputs", "move-sink-input", sink.name)
                    switched.append(sink.description)
        except Exception as exc:
            errors.append(str(exc))

    if source_query:
        try:
            sources = list_devices("sources")
            source = match_device(sources, source_query, exclude_monitors=True)
            if source is None:
                errors.append(f"Source not found: {source_query}")
            else:
                res = _run_pactl("set-default-source", source.name)
                if res.returncode != 0:
                    errors.append(res.stderr.strip() or f"Failed to set source {source.name}")
                else:
                    _move_active_streams("source-outputs", "move-source-output", source.name)
                    switched.append(source.description)
        except Exception as exc:
            errors.append(str(exc))

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        if osd:
            show_osd(errors[0], "dialog-warning")
        return 1

    if osd and (label or switched):
        show_osd(label or " + ".join(switched), icon)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="xppen-audio-switch",
        description="Switch default PipeWire/PulseAudio sink and/or source by name or description.",
    )
    parser.add_argument("--sink", help="Output device description/name substring to select")
    parser.add_argument("--source", help="Input device description/name substring to select")
    parser.add_argument("--label", help="Short label to show in the KDE OSD popup")
    parser.add_argument("--icon", default="audio-card", help="KDE icon name for OSD")
    parser.add_argument("--no-osd", action="store_true", help="Disable KDE OSD popup")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all detected sinks and non-monitor sources, then exit",
    )

    args = parser.parse_args(argv)

    if args.list:
        print("=== Sinks (Outputs) ===")
        for dev in list_devices("sinks"):
            print(f"  {dev.description}  [{dev.name}]")
        print("=== Sources (Inputs) ===")
        for dev in list_devices("sources"):
            if not dev.is_monitor:
                print(f"  {dev.description}  [{dev.name}]")
        return 0

    if not args.sink and not args.source:
        parser.error("Provide --sink, --source, or --list")

    return switch_audio(
        sink_query=args.sink,
        source_query=args.source,
        label=args.label,
        icon=args.icon,
        osd=not args.no_osd,
    )
