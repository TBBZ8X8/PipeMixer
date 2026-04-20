#!/usr/bin/env bash
set -e

echo "==> Installing PipeMixer"

# System dependencies
echo "==> Checking system dependencies..."
for cmd in wpctl kdotool playerctl ydotool; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  WARNING: '$cmd' not found — some features may not work"
        echo "           Install with: sudo pacman -S $cmd  (or yay -S $cmd)"
    fi
done

# Enable ydotool daemon
echo "==> Enabling ydotool daemon..."
systemctl --user enable --now ydotool 2>/dev/null || echo "  (ydotool daemon not available — arrow key simulation won't work)"

# Install pipemixer
echo "==> Installing Python package..."
if command -v pipx &>/dev/null; then
    pipx install --force .
else
    echo "  pipx not found, trying pip with --break-system-packages..."
    pip install --user --break-system-packages -e .
fi

# Install systemd service
echo "==> Installing systemd user service..."
mkdir -p "$HOME/.config/systemd/user"
cp pipemixer.service "$HOME/.config/systemd/user/pipemixer.service"
systemctl --user daemon-reload
systemctl --user enable pipemixer

echo ""
echo "Done! PipeMixer installed."
echo ""
echo "To start now:     systemctl --user start pipemixer"
echo "To view logs:     journalctl --user -u pipemixer -f"
echo "To stop:          systemctl --user stop pipemixer"
