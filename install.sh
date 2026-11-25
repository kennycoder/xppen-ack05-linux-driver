#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/xppen-ack05"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LOCAL_BIN="$HOME/.local/bin"

echo "==> Installing udev rule (requires sudo)..."
sudo rm -f /etc/udev/rules.d/99-xppen-ack05.rules
sudo install -m 644 "$SCRIPT_DIR/70-xppen-ack05.rules" /etc/udev/rules.d/70-xppen-ack05.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw
sudo udevadm trigger --action=add --subsystem-match=misc --sysname-match=uinput || true
sudo udevadm settle

echo "==> Installing CLI symlinks to $LOCAL_BIN..."
mkdir -p "$LOCAL_BIN"
chmod +x "$SCRIPT_DIR/xppen-ack05" "$SCRIPT_DIR/xppen-audio-switch"
ln -sfn "$SCRIPT_DIR/xppen-ack05" "$LOCAL_BIN/xppen-ack05"
ln -sfn "$SCRIPT_DIR/xppen-audio-switch" "$LOCAL_BIN/xppen-audio-switch"

echo "==> Installing default config (preserving any existing config)..."
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
    cp "$SCRIPT_DIR/config.toml" "$CONFIG_DIR/config.toml"
    echo "    Created $CONFIG_DIR/config.toml"
else
    echo "    Kept existing $CONFIG_DIR/config.toml"
fi

echo "==> Installing and enabling systemd user service..."
mkdir -p "$SYSTEMD_USER_DIR"
cp "$SCRIPT_DIR/xppen-ack05.service" "$SYSTEMD_USER_DIR/xppen-ack05.service"
systemctl --user daemon-reload
systemctl --user enable xppen-ack05.service
systemctl --user restart xppen-ack05.service

echo ""
echo "Done! Status:"
systemctl --user --no-pager status xppen-ack05.service || true
echo ""
echo "Edit your mappings at: $CONFIG_DIR/config.toml"
echo "Test hardware buttons: systemctl --user stop xppen-ack05 && $LOCAL_BIN/xppen-ack05 --dry-run"
