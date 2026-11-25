"""Unit tests for xppen_ack05 (key parsing, protocol decoding, dial modes, audio, config)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xppen_ack05.audio import AudioDevice, match_device
from xppen_ack05.config import load_config
from xppen_ack05.daemon import ButtonTracker
from xppen_ack05.keycodes import KEYCODES, REL_WHEEL, parse_action, resolve_keycode
from xppen_ack05.protocol import (
    INIT_REPORT,
    BatteryPacket,
    InputPacket,
    StatusPacket,
    UnknownPacket,
    decode_pressed_buttons,
    find_ack05_hidraw,
    parse_report,
)


class FakeEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def press_action(self, action) -> None:
        self.events.append(("press", action.raw))

    def release_action(self, action) -> None:
        self.events.append(("release", action.raw))

    def tap_action(self, action, hold_seconds: float = 0.0) -> None:
        self.events.append(("tap", action.raw))

    def release_all(self) -> None:
        self.events.append(("release_all", ""))


class KeycodesTest(unittest.TestCase):
    def test_aliases_and_chords(self) -> None:
        self.assertEqual(resolve_keycode("ctrl"), KEYCODES["KEY_LEFTCTRL"])
        self.assertEqual(resolve_keycode("KEY_SPACE"), KEYCODES["KEY_SPACE"])
        self.assertEqual(resolve_keycode("volumeup"), KEYCODES["KEY_VOLUMEUP"])

        act = parse_action("ctrl+shift+z")
        self.assertEqual(act.kind, "keys")
        self.assertEqual(
            act.keycodes,
            (KEYCODES["KEY_LEFTCTRL"], KEYCODES["KEY_LEFTSHIFT"], KEYCODES["KEY_Z"]),
        )

    def test_scroll_mod_scroll_cmd_and_mode_actions(self) -> None:
        up = parse_action("scroll_up")
        self.assertEqual(up.kind, "scroll")
        self.assertEqual(up.rel_axis, REL_WHEEL)
        self.assertEqual(up.rel_value, 1)
        self.assertEqual(up.keycodes, ())

        zoom_scroll = parse_action("ctrl+scroll_up")
        self.assertEqual(zoom_scroll.kind, "scroll")
        self.assertEqual(zoom_scroll.keycodes, (KEYCODES["KEY_LEFTCTRL"],))
        self.assertEqual(zoom_scroll.rel_axis, REL_WHEEL)
        self.assertEqual(zoom_scroll.rel_value, 1)

        cmd = parse_action('cmd:xppen-audio-switch --sink "Stage V2"')
        self.assertEqual(cmd.kind, "cmd")
        self.assertEqual(cmd.command, 'xppen-audio-switch --sink "Stage V2"')

        nxt = parse_action("dial_mode_next")
        self.assertEqual(nxt.kind, "dial_mode")
        self.assertEqual(nxt.mode_target, "next")

        named = parse_action("dial_mode:Zoom")
        self.assertEqual(named.kind, "dial_mode")
        self.assertEqual(named.mode_target, "Zoom")

        none = parse_action("none")
        self.assertEqual(none.kind, "none")

    def test_invalid_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_action("ctrl+definitely_not_a_real_key")


class ProtocolTest(unittest.TestCase):
    def test_init_report_length_and_header(self) -> None:
        self.assertEqual(len(INIT_REPORT), 64)
        self.assertEqual(INIT_REPORT[:3], b"\x02\xb0\x04")

    def test_parse_input_packet(self) -> None:
        # k1 (bit 0) + k10 (bit 9 -> 0x0200) + dial CW (0x01 at byte 7)
        raw = bytes([0x02, 0xF0, 0x01, 0x02, 0x00, 0x00, 0x00, 0x01, 0, 0, 0, 0])
        pkt = parse_report(raw)
        self.assertIsInstance(pkt, InputPacket)
        assert isinstance(pkt, InputPacket)
        self.assertEqual(pkt.button_mask, 0x0201)
        self.assertEqual(pkt.dial, "dial_cw")
        self.assertEqual(decode_pressed_buttons(pkt.button_mask), {"k1", "k10"})

    def test_parse_dial_ccw_and_dial_button(self) -> None:
        # dial_button (bit 10 -> 0x0400) + dial CCW (0x02)
        raw = bytes([0x02, 0xF0, 0x00, 0x04, 0x00, 0x00, 0x00, 0x02, 0, 0, 0, 0])
        pkt = parse_report(raw)
        assert isinstance(pkt, InputPacket)
        self.assertEqual(pkt.dial, "dial_ccw")
        self.assertEqual(decode_pressed_buttons(pkt.button_mask), {"dial_button"})

    def test_parse_status_and_battery(self) -> None:
        wake = parse_report(bytes([0x02, 0xF8, 0x02, 0x01, 0, 0, 0, 0, 0, 0, 0, 0]))
        self.assertIsInstance(wake, StatusPacket)
        assert isinstance(wake, StatusPacket)
        self.assertTrue(wake.connected)
        self.assertEqual((wake.code, wake.value), (0x02, 0x01))

        sleep = parse_report(bytes([0x02, 0xF8, 0x04, 0x01, 0, 0, 0, 0, 0, 0, 0, 0]))
        self.assertIsInstance(sleep, StatusPacket)
        assert isinstance(sleep, StatusPacket)
        self.assertFalse(sleep.connected)
        self.assertEqual((sleep.code, sleep.value), (0x04, 0x01))

        bat = parse_report(bytes([0x02, 0xF2, 0x01, 85]))
        assert isinstance(bat, BatteryPacket)
        self.assertEqual(bat.percent, 85)
        self.assertTrue(bat.charging)

        unk = parse_report(bytes([0x09, 0x01]))
        self.assertIsInstance(unk, UnknownPacket)

    def test_find_ack05_hidraw_uses_vendor_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, phys, desc in [
                ("hidraw1", "usb-1/input0", b"\x05\x01\x09\x02"),
                ("hidraw2", "usb-1/input1", b"\x05\x0d\x09\x02"),
                ("hidraw3", "usb-1/input2", b"\x06\x0a\xff\x09\x01"),
            ]:
                dev = root / name / "device"
                dev.mkdir(parents=True)
                (dev / "uevent").write_text(
                    f"HID_ID=0003:000028BD:00000202\nHID_PHYS={phys}\n"
                )
                (dev / "report_descriptor").write_bytes(desc)

            found = find_ack05_hidraw(sysfs_root=root)
            self.assertEqual(found, Path("/dev/hidraw3"))


class TrackerAndConfigTest(unittest.TestCase):
    def test_repo_config_audio_commands_and_dial_modes(self) -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "config.toml"
        cfg = load_config(cfg_path)
        self.assertEqual([m.name for m in cfg.dial_modes], ["Volume", "Zoom", "Scroll"])

        emitter = FakeEmitter()
        ran_cmds: list[str] = []
        osds: list[tuple[str, str]] = []
        tracker = ButtonTracker(
            emitter,  # type: ignore[arg-type]
            cfg,
            command_runner=ran_cmds.append,
            osd_notifier=lambda text, icon: osds.append((text, icon)),
        )

        # Press + release K1 -> runs Fiio K17 + FOX switcher once
        tracker.handle_packet(InputPacket(button_mask=0x0001, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))
        # Press + release K2 -> runs Audeze Maxwell switcher
        tracker.handle_packet(InputPacket(button_mask=0x0002, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))
        # Press + release K3 -> runs Stage V2 + FOX switcher
        tracker.handle_packet(InputPacket(button_mask=0x0004, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))

        self.assertEqual(len(ran_cmds), 3)
        self.assertIn("Fiio K17 R2R Analog Stereo", ran_cmds[0])
        self.assertIn("FOX Mono", ran_cmds[0])
        self.assertIn("Audeze Maxwell Dongle Analog Stereo", ran_cmds[1])
        self.assertIn("Audeze Maxwell Dongle Mono", ran_cmds[1])
        self.assertIn("Stage V2 Analog Stereo", ran_cmds[2])
        self.assertIn("FOX Mono", ran_cmds[2])

        # Mode 0 (Volume): CW/CCW -> volumeup / volumedown
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_cw", raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_ccw", raw=b""))

        # Press dial_button (bit 10 -> 0x0400) -> Mode 1 (Zoom)
        tracker.handle_packet(InputPacket(button_mask=0x0400, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_cw", raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_ccw", raw=b""))

        # Press dial_button -> Mode 2 (Scroll)
        tracker.handle_packet(InputPacket(button_mask=0x0400, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_cw", raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial="dial_ccw", raw=b""))

        # Reload config preserves Scroll mode
        tracker.update_config(cfg)
        self.assertEqual(tracker.active_dial_mode.name, "Scroll")

        # Press dial_button -> wraps back to Mode 0 (Volume)
        tracker.handle_packet(InputPacket(button_mask=0x0400, dial=None, raw=b""))
        tracker.handle_packet(InputPacket(button_mask=0x0000, dial=None, raw=b""))
        self.assertEqual(tracker.active_dial_mode.name, "Volume")

        self.assertEqual(
            emitter.events,
            [
                ("tap", "volumeup"),
                ("tap", "volumedown"),
                ("tap", "ctrl+equal"),
                ("tap", "ctrl+minus"),
                ("tap", "scroll_down"),
                ("tap", "scroll_up"),
                ("release_all", ""),
            ],
        )
        self.assertEqual(
            osds,
            [
                ("Dial: Zoom", "zoom-in"),
                ("Dial: Scroll", "input-mouse"),
                ("Dial: Volume", "audio-volume-high"),
            ],
        )

    def test_invalid_control_name_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.toml"
            p.write_text('[keys]\nk99 = "a"\n')
            with self.assertRaises(ValueError):
                load_config(p)


class AudioMatcherTest(unittest.TestCase):
    def test_match_user_sinks_and_sources_skipping_monitors(self) -> None:
        sinks = [
            AudioDevice("alsa_output.usb-FIIO_K17-00.analog-stereo", "FIIO K17 R2R Analog Stereo"),
            AudioDevice("alsa_output.usb-Audeze_Maxwell-00.analog-stereo", "Audeze Maxwell Dongle Analog Stereo"),
            AudioDevice("alsa_output.usb-Creative_Stage_V2-00.analog-stereo", "Stage V2 Analog Stereo"),
        ]
        sources = [
            AudioDevice("alsa_output.usb-Audeze_Maxwell-00.analog-stereo.monitor", "Monitor of Audeze Maxwell Dongle Analog Stereo", is_monitor=True),
            AudioDevice("alsa_input.usb-Beyerdynamic_FOX-00.mono-fallback", "FOX Mono", is_monitor=False),
            AudioDevice("alsa_input.usb-Audeze_Maxwell-00.mono-fallback", "Audeze Maxwell Dongle Mono", is_monitor=False),
        ]

        self.assertEqual(
            match_device(sinks, "Fiio K17 R2R Analog Stereo").name,
            "alsa_output.usb-FIIO_K17-00.analog-stereo",
        )
        self.assertEqual(
            match_device(sinks, "Audeze Maxwell Dongle Analog Stereo").name,
            "alsa_output.usb-Audeze_Maxwell-00.analog-stereo",
        )
        self.assertEqual(
            match_device(sinks, "Stage V2 Analog Stereo").name,
            "alsa_output.usb-Creative_Stage_V2-00.analog-stereo",
        )
        self.assertEqual(
            match_device(sources, "FOX Mono", exclude_monitors=True).name,
            "alsa_input.usb-Beyerdynamic_FOX-00.mono-fallback",
        )
        self.assertEqual(
            match_device(sources, "Audeze Maxwell Dongle Mono", exclude_monitors=True).name,
            "alsa_input.usb-Audeze_Maxwell-00.mono-fallback",
        )


if __name__ == "__main__":
    unittest.main()
