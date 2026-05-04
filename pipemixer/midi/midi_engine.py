"""
midi_engine.py — MIDI controller engine for PipeMixer

Loads a device profile JSON that defines note mappings and actions,
making it easy to support other controllers in future by adding a
new profile file.
"""

import json
import os
import subprocess
import threading
import time

import rtmidi

from pipemixer.audio.pipewire_controller import PipeWireController


# ── Profile loading ───────────────────────────────────────────────────────────

DEFAULT_PROFILE = os.path.join(
    os.path.dirname(__file__),
    "../../profiles/nanokontrol2_mackie.json"
)


def load_profile(path: str | None = None) -> dict:
    """Load a device profile JSON. Falls back to nanoKONTROL2 Mackie."""
    # Check user config dir first
    user_profile = os.path.expanduser("~/.config/pipemixer/profiles/nanokontrol2_mackie.json")

    profile_path = path or os.environ.get("PIPEMIXER_PROFILE") or user_profile

    try:
        with open(os.path.normpath(profile_path)) as f:
            profile = json.load(f)
        print(f"Loaded profile: {profile.get('name', profile_path)}")
        return profile
    except FileNotFoundError:
        print("Using built-in nanoKONTROL2 Mackie defaults")
        return _default_profile()
    except Exception as e:
        print(f"Failed to load profile {profile_path}: {e}")
        print("Using built-in nanoKONTROL2 Mackie defaults")
        return _default_profile()


def _default_profile() -> dict:
    return {
        "name": "Korg nanoKONTROL2 (Mackie mode) [built-in fallback]",
        "device_match": "nanoKONTROL",
        "leds": {"rec_base": 0, "solo_base": 8, "mute_base": 16},
        "transport": {
            "prev_track": 46, "next_track": 47, "cycle": 89,
            "rew": 91, "ff": 92, "stop": 93, "play": 94, "rec": 95,
        },
        "transport_actions": {
            "prev_track": "playerctl_previous",
            "next_track": "playerctl_next",
            "cycle":      "unassigned",
            "rew":        "key_left",
            "ff":         "key_right",
            "stop":       "playerctl_stop",
            "play":       "playerctl_play_pause",
            "rec":        "mute_all",
        },
        "channel_buttons": {
            "rec_note_base": 0, "solo_note_base": 8, "mute_note_base": 16,
        },
        "sliders": {"type": "pitchbend", "count": 8},
    }


# ── ydotool key codes ─────────────────────────────────────────────────────────
KEY_LEFT  = 105
KEY_RIGHT = 106


class MidiEngine:

    def __init__(
        self,
        num_channels: int = 8,
        midi_in_port_name: str | None = None,
        midi_out_port_name: str | None = None,
        profile_path: str | None = None,
    ):
        self.pw = PipeWireController()
        self.num_channels = num_channels

        # Load device profile
        self.profile = load_profile(profile_path)
        self._init_note_map()

        # Channel state
        self.slider_app    = [None]  * num_channels
        self.slider_values = [1.0]   * num_channels
        self.slider_muted  = [False] * num_channels
        self.slider_soloed = [False] * num_channels
        self._all_muted    = False
        self._last_value   = [0] * 128

        # Per-channel app history for deferred rebind
        # BUG FIX: when an app is moved to a new channel, it is removed
        # from the old channel's history so it only rebinds to the new one.
        self.slider_app_history: list[list[str]] = [[] for _ in range(num_channels)]

        self.midi_in  = rtmidi.MidiIn()
        self.midi_out = rtmidi.MidiOut()
        self._open_midi_ports(midi_in_port_name, midi_out_port_name)

        self._update_led_all()
        threading.Thread(target=self._poll_midi,             daemon=True).start()
        threading.Thread(target=self._auto_unbind_loop,      daemon=True).start()
        threading.Thread(target=self._deferred_rebind_loop,  daemon=True).start()

        self._restore_bindings()
        threading.Timer(0.5, self._update_led_all).start()

    # ----------------------------------------------------------------- Profile

    def _init_note_map(self) -> None:
        """Build reverse lookup: note number -> action name."""
        t = self.profile.get("transport", {})
        a = self.profile.get("transport_actions", {})
        cb = self.profile.get("channel_buttons", {})
        leds = self.profile.get("leds", {})

        # Note -> action name
        self.note_action: dict[int, str] = {}
        for action_key, note in t.items():
            if isinstance(note, int):
                self.note_action[note] = a.get(action_key, "unassigned")

        # Channel button base notes
        self.rec_note_base  = cb.get("rec_note_base",  0)
        self.solo_note_base = cb.get("solo_note_base", 8)
        self.mute_note_base = cb.get("mute_note_base", 16)

        # LED base notes
        self.led_rec_base  = leds.get("rec_base",  0)
        self.led_solo_base = leds.get("solo_base", 8)
        self.led_mute_base = leds.get("mute_base", 16)

        # Slider type
        self.slider_type = self.profile.get("sliders", {}).get("type", "pitchbend")

    # ------------------------------------------------------------------ Ports

    def _open_midi_ports(self, in_name, out_name) -> None:
        in_ports  = self.midi_in.get_ports()
        out_ports = self.midi_out.get_ports()

        print("\nAvailable MIDI INPUT ports:")
        for i, name in enumerate(in_ports):
            print(f"  [{i}] {name}")
        print("Available MIDI OUTPUT ports:")
        for i, name in enumerate(out_ports):
            print(f"  [{i}] {name}")

        match = self.profile.get("device_match", "nanoKONTROL")
        in_idx  = next((i for i, n in enumerate(in_ports)  if match in n), 1)
        out_idx = next((i for i, n in enumerate(out_ports) if match in n), 1)

        self.midi_in.open_port(in_idx)
        self.midi_out.open_port(out_idx)
        print(f"Opened MIDI input:  {in_ports[in_idx]}")
        print(f"Opened MIDI output: {out_ports[out_idx]}")

    # --------------------------------------------------------- Binding persistence

    def _restore_bindings(self) -> None:
        self.slider_app_history = self.pw.load_bindings(self.num_channels)
        active_apps = self.pw.get_apps()
        for i, app_list in enumerate(self.slider_app_history):
            for app_name in app_list:
                if app_name in active_apps:
                    print(f"Restoring channel {i} → {app_name}")
                    self.slider_app[i] = app_name
                    self._apply_channel_volume(i)
                    break
            else:
                if app_list:
                    print(f"Channel {i}: saved apps {app_list} not running, waiting...")
        self._update_led_all()

    def _save_bindings(self) -> None:
        self.pw.save_bindings(self.slider_app_history)

    # ------------------------------------------------------------------- Bind

    def bind_slider_to_app(self, channel_index: int, app_name: str) -> None:
        current = self.slider_app[channel_index]

        # Toggle off if already bound to this app on this channel
        if current == app_name:
            print(f"Channel {channel_index} unbound from {app_name}")
            self.slider_app[channel_index] = None
            self.pw.set_volume(app_name, 1.0)
            # Keep in history so deferred rebind still works
            self._update_led_all()
            return

        if not app_name:
            print("Bind failed: no app")
            return

        # BUG FIX: Remove app from any other channel's active slot AND history.
        # This ensures the app only auto-rebinds to this channel in future,
        # not the old one. (e.g. moving Minecraft from ch1 to ch2 means next
        # time Minecraft opens it goes to ch2, not ch1.)
        for i in range(self.num_channels):
            if i == channel_index:
                continue
            if self.slider_app[i] == app_name:
                self.slider_app[i] = None
                self._update_led(i)
            if app_name in self.slider_app_history[i]:
                self.slider_app_history[i].remove(app_name)

        # Add to this channel's history if not already there
        if app_name not in self.slider_app_history[channel_index]:
            self.slider_app_history[channel_index].append(app_name)

        self.slider_app[channel_index] = app_name
        print(f"Channel {channel_index} bound to {app_name}")
        self._apply_channel_volume(channel_index)
        self._save_bindings()
        self._update_led_all()

    # ------------------------------------------------------- Auto-unbind loop

    def _auto_unbind_loop(self) -> None:
        while True:
            active_apps = self.pw.get_apps()
            for i, app_name in enumerate(self.slider_app):
                if app_name and app_name not in active_apps:
                    print(f"Channel {i} auto-unbinding {app_name} (app closed)")
                    self.slider_app[i] = None
                    self.pw.set_volume(app_name, 1.0)
                    self._update_led(i)
            time.sleep(1.0)

    # ---------------------------------------------------- Deferred rebind loop

    def _deferred_rebind_loop(self) -> None:
        """
        Watch for saved apps that weren't running at startup and
        auto-bind them when they appear. Each channel can have multiple
        saved apps — first one found running wins.
        """
        while True:
            time.sleep(3.0)
            active_apps = self.pw.get_apps()
            for i, app_list in enumerate(self.slider_app_history):
                if self.slider_app[i] is not None:
                    continue
                for app_name in app_list:
                    if app_name in active_apps:
                        print(f"Deferred rebind: channel {i} → {app_name}")
                        self.slider_app[i] = app_name
                        self._apply_channel_volume(i)
                        self._update_led(i)
                        break

    # ------------------------------------------------------------------- Mute

    def toggle_mute(self, channel_index: int) -> None:
        self.slider_muted[channel_index] = not self.slider_muted[channel_index]
        print(f"Channel {channel_index} mute={self.slider_muted[channel_index]}")
        self._apply_channel_volume(channel_index)
        self._update_led_all()

    def toggle_mute_all(self) -> None:
        self._all_muted = not self._all_muted
        print(f"Mute all = {self._all_muted}")
        for i in range(self.num_channels):
            self.slider_muted[i] = self._all_muted
            self._apply_channel_volume(i)
        self._update_led_all()

    # ------------------------------------------------------------------- Solo

    def toggle_solo(self, channel_index: int) -> None:
        if self.slider_soloed[channel_index]:
            self.slider_soloed = [False] * self.num_channels
            self._restore_all_channels()
            print(f"Channel {channel_index} solo cleared")
        else:
            self.slider_soloed = [False] * self.num_channels
            self.slider_soloed[channel_index] = True
            self._apply_solo_state()
            print(f"Channel {channel_index} solo active")
        self._update_led_all()

    # ----------------------------------------------------------------- Slider

    def slider_moved(self, channel_index: int, midi_value: int) -> None:
        self.slider_values[channel_index] = midi_value / 127.0
        self._update_led(channel_index)

    # --------------------------------------------------------------- Volume

    def _apply_channel_volume(self, channel_index: int) -> None:
        app_name = self.slider_app[channel_index]
        if not app_name:
            return
        base = self.slider_values[channel_index]
        if self.slider_muted[channel_index]:
            volume = 0.0
        elif any(self.slider_soloed):
            volume = base if self.slider_soloed[channel_index] else 0.0
        else:
            volume = base
        self.pw.set_volume(app_name, volume)

    def _apply_solo_state(self) -> None:
        for i in range(self.num_channels):
            self._apply_channel_volume(i)

    def _restore_all_channels(self) -> None:
        for i in range(self.num_channels):
            self._apply_channel_volume(i)

    # ------------------------------------------------------------------- LEDs

    def _send_led(self, note: int, state: bool) -> None:
        self.midi_out.send_message([0x90, note, 127 if state else 0])

    def _update_led(self, channel_index: int) -> None:
        self._send_led(self.led_rec_base  + channel_index, self.slider_app[channel_index] is not None)
        self._send_led(self.led_solo_base + channel_index, self.slider_soloed[channel_index])
        self._send_led(self.led_mute_base + channel_index, self.slider_muted[channel_index])

    def _update_led_all(self) -> None:
        for i in range(self.num_channels):
            self._update_led(i)

    # --------------------------------------------------------------- Transport

    def _run(self, *cmd: str) -> None:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _press_key(self, keycode: int) -> None:
        self._run("ydotool", "key", str(keycode))

    def _dispatch_action(self, action: str) -> None:
        """Execute a transport action by name (from profile JSON)."""
        if action == "playerctl_play_pause":
            self._run("playerctl", "play-pause")
        elif action == "playerctl_stop":
            self._run("playerctl", "stop")
        elif action == "playerctl_previous":
            self._run("playerctl", "previous")
        elif action == "playerctl_next":
            self._run("playerctl", "next")
        elif action == "key_left":
            self._press_key(KEY_LEFT)
        elif action == "key_right":
            self._press_key(KEY_RIGHT)
        elif action == "mute_all":
            self.toggle_mute_all()
        elif action == "unassigned":
            pass
        else:
            print(f"Unknown action in profile: {action}")

    # ---------------------------------------------------------------- Polling

    def _poll_midi(self) -> None:
        last_tick = time.time()
        while True:
            while True:
                msg = self.midi_in.get_message()
                if not msg:
                    break
                data, _ = msg
                self._handle_midi_message(data)
            now = time.time()
            if now - last_tick >= 0.016:
                last_tick = now
                for i in range(self.num_channels):
                    self._apply_channel_volume(i)
            time.sleep(0.0005)

    # -------------------------------------------------------------- Handler

    def _handle_midi_message(self, data: list[int]) -> None:
        if len(data) < 3:
            return

        status, data1, data2 = data
        msg_type = status & 0xF0

        prev = self._last_value[data1]
        self._last_value[data1] = data2

        # Pitch bend → sliders (nanoKONTROL2 Mackie mode)
        if msg_type == 0xE0 and self.slider_type == "pitchbend":
            ch = status & 0x0F
            if ch < self.num_channels:
                self.slider_values[ch] = (data1 | (data2 << 7)) / 16383.0

        elif msg_type == 0x90:
            if data2 == 0:
                return  # ignore release

            note = data1
            rec_end  = self.rec_note_base  + self.num_channels - 1
            solo_end = self.solo_note_base + self.num_channels - 1
            mute_end = self.mute_note_base + self.num_channels - 1

            if self.rec_note_base <= note <= rec_end:
                self._handle_bind(note - self.rec_note_base)
            elif self.solo_note_base <= note <= solo_end:
                self.toggle_solo(note - self.solo_note_base)
            elif self.mute_note_base <= note <= mute_end:
                self.toggle_mute(note - self.mute_note_base)
            elif note in self.note_action:
                self._dispatch_action(self.note_action[note])

    def _handle_bind(self, channel_index: int) -> None:
        focused_app = self.pw.get_focused_app()
        if not focused_app:
            focused_app = self.pw.get_best_app()
        if not focused_app:
            print("No app available to bind")
            return
        self.bind_slider_to_app(channel_index, focused_app)
