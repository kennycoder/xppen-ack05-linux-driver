# Disclaimer

This project might contain traces of AI. If you are allergic, please consult your local doctor.

# xppen-ack05

Lightweight, Linux userspace driver for the **XPPen ACK05 Wireless Shortcut Remote** (`28bd:0202`) over USB dongle or cable. Original software offered by XPPen is, cough.. cough, not good imho.

Anyway, this works natively on most linux distros that but I only tested on Debian.

How it works? Glad you asked..

1. Discovers the ACK05 vendor HID interface (`0xFF0A`, `/dev/hidraw*`).
2. Sends the vendor bitmask initialization packet (`02 b0 04 ...`), which disables the hardcoded factory shortcuts and exposes all 10 keys, the dial center button, and dial CW/CCW independently.
3. Translates hardware events to virtual keyboard & scroll events through `/dev/uinput` using your TOML configuration.

## Quick Setup

```bash
chmod +x install.sh
./install.sh
```

This will:
- Install `/etc/udev/rules.d/70-xppen-ack05.rules` (grants your desktop user access to `28bd:0202` without running as root) and reload udev.
- Symlink `./xppen-ack05` to `~/.local/bin/xppen-ack05`.
- Copy `config.toml` to `~/.config/xppen-ack05/config.toml` (only if not already present).
- Install and start the `xppen-ack05.service` systemd user service.

## Customizing Keys & Dial Modes

Edit `~/.config/xppen-ack05/config.toml` — changes hot-reload automatically when saved:

```toml
[keys]
k1 = 'cmd:xppen-audio-switch --sink "Fiio K17 R2R Analog Stereo" --source "FOX Mono" --label "FiiO K17 + FOX"'
k2 = 'cmd:xppen-audio-switch --sink "Audeze Maxwell Dongle Analog Stereo" --source "Audeze Maxwell Dongle Mono" --label "Audeze Maxwell"'
k3 = 'cmd:xppen-audio-switch --sink "Stage V2 Analog Stereo" --source "FOX Mono" --label "Stage V2 + FOX"'
dial_button = "dial_mode_next"

[[dial_modes]]
name = "Volume"
icon = "audio-volume-high"
cw   = "volumeup"
ccw  = "volumedown"

[[dial_modes]]
name = "Zoom"
icon = "zoom-in"
cw   = "ctrl+equal"
ccw  = "ctrl+minus"

[[dial_modes]]
name = "Scroll"
icon = "input-mouse"
cw   = "scroll_down"
ccw  = "scroll_up"
```

List all recognized key names or detected audio devices:

```bash
./xppen-ack05 --list-keys
./xppen-audio-switch --list
```

## Verifying Physical Buttons (Debug Mode)

If you want to confirm which physical button corresponds to `k1`–`k10` and `dial_button` on your remote (or if your firmware revision orders bits differently):

```bash
systemctl --user stop xppen-ack05
./xppen-ack05 --dry-run
```

Press each button and turn the dial to see the raw packet, bit index, and logical button name. If any button maps to a different bit index, override it under `[hardware.button_bits]` in `~/.config/xppen-ack05/config.toml`, then restart the service:

```bash
systemctl --user start xppen-ack05
```

## Diagnostics

```bash
./xppen-ack05 --check
journalctl --user -u xppen-ack05 -f
```
# xppen-ack05-linux-driver
