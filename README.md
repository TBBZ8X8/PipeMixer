# PipeMixer

Per-app volume mixer for Linux, controlled by a Korg nanoKONTROL2 (Mackie mode).

## Requirements

**System tools** (install via pacman/yay):
- `wpctl` — PipeWire control (part of `wireplumber`)
- `kdotool` — focused window detection on KDE Wayland (`yay -S kdotool`)
- `playerctl` — media player control (`sudo pacman -S playerctl`)
- `ydotool` — key simulation on Wayland (`sudo pacman -S ydotool`)

**Python**:
- `python-rtmidi` (installed automatically)

## Install

```bash
git clone https://github.com/yourname/pipemixer
cd pipemixer
chmod +x install.sh
./install.sh
```

## Usage

```bash
# Start manually
pipemixer

# Or via systemd (auto-starts on login after install)
systemctl --user start pipemixer
journalctl --user -u pipemixer -f
```

## nanoKONTROL2 setup

The device must be in **Mackie Control mode** with **LED mode set to External**.

To enter Mackie mode: hold SET MARKER + FF while connecting USB.

## Button mapping

| Button | Action |
|--------|--------|
| R (per channel) | Bind focused app to slider |
| M (per channel) | Mute channel |
| S (per channel) | Solo channel |
| ▶ Play | Play / pause (playerctl) |
| ■ Stop | Stop (playerctl) |
| ● Rec | Mute / unmute all channels |
| ◀◀ Rew | Left arrow key |
| ▶▶ FF | Right arrow key |
| ◀◀\| Prev track | Previous track (playerctl) |
| \|▶▶ Next track | Next track (playerctl) |
