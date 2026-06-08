# PipeMixer

A MIDI-controlled per-app volume mixer for Linux, built as an open source alternative to [MidiMixer](https://midimixer.app/) which doesn't support Linux.

Control individual application volumes using a hardware MIDI controller — bind sliders to apps, mute, solo, and use transport buttons for media control.

> **Note:** This project was vibecoded with [Claude](https://claude.ai). The author is not a professional developer — contributions, bug reports, and code reviews are very welcome.

---

## Features

- Per-app volume control via MIDI sliders
- Bind any app to any channel by focusing it and pressing R
- Auto-rebind on startup — channels remember their apps across reboots
- Multi-app channel history — assign multiple apps to one channel (e.g. Minecraft and Satisfactory on the same slider), whichever is open gets bound automatically
- Hold R for 1 second to clear a channel's history
- Mute, solo per channel
- Transport buttons: play/pause, stop, prev/next track, seek (arrow keys)
- Mute-all toggle on the transport REC button
- LED feedback on all buttons
- Cross-DE focus detection (KDE Wayland, X11, Sway, Hyprland, GNOME with extension)
- MIDI device profile system — mappings stored in JSON, easy to customise

---

## Requirements

### Hardware
- Korg nanoKONTROL2 (must be in **Mackie Control mode** — see setup below)
- Linux with PipeWire

### System tools
Install via your package manager:

| Tool | Purpose | Arch/CachyOS | Debian/Ubuntu |
|------|---------|--------------|---------------|
| `wireplumber` | PipeWire control (`wpctl`) | `sudo pacman -S wireplumber` | `sudo apt install wireplumber` |
| `kdotool` | Focus detection (KDE Wayland) | `yay -S kdotool` | build from source |
| `xdotool` | Focus detection (X11) | `sudo pacman -S xdotool` | `sudo apt install xdotool` |
| `playerctl` | Media control | `sudo pacman -S playerctl` | `sudo apt install playerctl` |
| `ydotool` | Key simulation (arrow keys) | `sudo pacman -S ydotool` | `sudo apt install ydotool` |

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/TBBZ8X8/PipeMixer/main/install.sh | bash
```

The installer will:
- Detect your distro and desktop environment
- Prompt before installing any missing dependencies
- Install PipeMixer via pipx (isolated Python environment)
- Set up a systemd user service that starts on login

To inspect the script before running it:
```bash
curl -fsSL https://raw.githubusercontent.com/TBBZ8X8/PipeMixer/main/install.sh | less
```

---

## nanoKONTROL2 Setup

The device must be in **Mackie Control mode** with **LED mode set to External**.

**To enter Mackie mode:**
1. Unplug the USB cable
2. Hold **SET** + **MARKER ▶** simultaneously
3. While holding both buttons, plug the USB cable back in
4. Release the buttons — the device is now in Mackie mode

You can verify it worked by running PipeMixer and checking the logs:
```bash
journalctl --user -u pipemixer -f
```
You should see `Loaded profile: Korg nanoKONTROL2 (Mackie mode)`.

---

## Usage

PipeMixer starts automatically on login via systemd. To control it manually:

```bash
# View logs live
journalctl --user -u pipemixer -f

# Restart
systemctl --user restart pipemixer

# Stop
systemctl --user stop pipemixer
```

---

## Button Mapping

| Button | Action |
|--------|--------|
| **R** (per channel) | Bind focused app to slider |
| **R** (hold 1s) | Clear channel history |
| **M** (per channel) | Mute channel |
| **S** (per channel) | Solo channel |
| **▶ Play** | Play / pause (playerctl) |
| **■ Stop** | Stop (playerctl) |
| **● Rec** | Mute / unmute all channels |
| **◀◀ Rew** | Left arrow key |
| **▶▶ FF** | Right arrow key |
| **◀◀\| Prev track** | Previous track (playerctl) |
| **\|▶▶ Next track** | Next track (playerctl) |

---

## GNOME Wayland

Focus detection on GNOME Wayland requires the **window-calls** extension:

1. Install from: https://extensions.gnome.org/extension/4724/window-calls/
2. Restart PipeMixer: `systemctl --user restart pipemixer`

Without it, PipeMixer will prompt you to use the "best app" fallback on first run.

---

## Customising Button Mappings

Edit `~/.config/pipemixer/profiles/nanokontrol2_mackie.json` to remap transport buttons. Available actions:

- `playerctl_play_pause`
- `playerctl_stop`
- `playerctl_previous`
- `playerctl_next`
- `key_left`
- `key_right`
- `mute_all`
- `unassigned`

Restart PipeMixer after editing.

---


## Manual App Overrides

Some apps can't be detected automatically — for example, Steam games where
the window class is `steam_app_XXXXXXX` but PipeWire uses a completely
different name like `elitedangerous64`.

Create `~/.config/pipemixer/overrides.json` to map window class names to
their PipeWire names manually:

```json
{
  "steam_app_359320": "elitedangerous64",
  "steam_app_812140": "elden ring.exe"
}
```

**How to find the values:**

Window class (the key) — run this while the app is focused:
```bash
sleep 3 && kdotool getactivewindow | xargs kdotool getwindowclassname
```

PipeWire name (the value) — run this while the app is playing audio:
```bash
pactl list sink-inputs | grep -E "application.name|node.name"
```

Use the value from `application.name` if present, otherwise `node.name`.

After saving the file, restart PipeMixer:
```bash
systemctl --user restart pipemixer
```

> **Tip:** If you find an override that works for a well-known game, consider
> opening an issue or pull request to get it added to the built-in matching.

---

## Uninstall

```bash
systemctl --user stop pipemixer
systemctl --user disable pipemixer
rm ~/.config/systemd/user/pipemixer.service
pipx uninstall pipemixer
rm -rf ~/.config/pipemixer
```

---

## Roadmap

- [ ] v0.4.x — Installer updates, README, single-file curl install
- [ ] v0.5.0 — `pulsectl` event-driven volume (no polling)
- [ ] v2.0.0 — Rust rewrite using `pipewire-rs` + `midir` (single binary, no Python)

---

## Contributing

Bug reports and pull requests welcome. Please open an issue before submitting large changes.

Since this project was vibecoded, the author genuinely appreciates code reviews and architectural feedback — don't be shy.

---

## License

MIT
