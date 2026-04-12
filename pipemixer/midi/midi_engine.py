import time
import subprocess
import threading
import rtmidi
from pipemixer.audio.pipewire_controller import PipeWireController


# nanoKONTROL2 LED note numbers (Mackie mode)
# Note On 0x90 velocity 127 = LED on, velocity 0 = LED off
LED_REC_BASE  = 0    # R LEDs: notes 0–7
LED_SOLO_BASE = 8    # S LEDs: notes 8–15
LED_MUTE_BASE = 16   # M LEDs: notes 16–23

LED_ON  = 127
LED_OFF = 0

# Transport button note numbers (Mackie mode, confirmed by amidi capture)
NOTE_PREV_TRACK = 46
NOTE_NEXT_TRACK = 47
NOTE_CYCLE      = 89   # unassigned for now
NOTE_REW        = 91
NOTE_FF         = 92
NOTE_STOP       = 93
NOTE_PLAY       = 94
NOTE_REC        = 95
# NOTE_PREV_MARK — multi-note composite, unusable in Mackie mode
# NOTE_NEXT_MARK — multi-note composite, unusable in Mackie mode
# NOTE_SET       — silent in Mackie mode

# ydotool key codes
KEY_LEFT  = 105
KEY_RIGHT = 106


class MidiEngine:
    def __init__(self, num_channels=8, midi_in_port_name=None, midi_out_port_name=None):
        self.pw = PipeWireController()
        self.num_channels = num_channels

        # ---------------- Channel state ----------------
        self.slider_app    = [None]  * num_channels
        self.slider_values = [1]     * num_channels
        self.slider_muted  = [False] * num_channels
        self.slider_soloed = [False] * num_channels

        # ---------------- Transport state ----------------
        self._all_muted = False

        # ---------------- Edge detection ----------------
        self._last_cc_value = [0] * 128

        # ---------------- MIDI Setup ----------------
        self.midi_in  = rtmidi.MidiIn()
        self.midi_out = rtmidi.MidiOut()

        self._open_midi_ports(midi_in_port_name, midi_out_port_name)

        # Initialize LEDs to known state
        self._update_led_all()

        # Start threads
        threading.Thread(target=self._poll_midi,        daemon=True).start()
        threading.Thread(target=self._auto_unbind_loop, daemon=True).start()

        # Force clean startup state
        self.slider_muted  = [False] * self.num_channels
        self.slider_soloed = [False] * self.num_channels
        self.slider_app    = [None]  * self.num_channels

        # Reset LEDs AFTER a short delay (let device settle)
        threading.Timer(0.5, self._update_led_all).start()

    # ---------------- MIDI Ports ----------------
    def _open_midi_ports(self, in_name, out_name):
        print("\nAvailable MIDI INPUT ports:")
        for i, name in enumerate(self.midi_in.get_ports()):
            print(f"[{i}] {name}")

        print("\nAvailable MIDI OUTPUT ports:")
        for i, name in enumerate(self.midi_out.get_ports()):
            print(f"[{i}] {name}")

        # TEMP: manually pick correct port index
        self.midi_in.open_port(1)
        print(f"Opened MIDI input:  {self.midi_in.get_ports()[1]}")

        self.midi_out.open_port(1)
        print(f"Opened MIDI output: {self.midi_out.get_ports()[1]}")

    # ---------------- Bind (R) ----------------
    def bind_slider_to_app(self, channel_index, app_name):
        current = self.slider_app[channel_index]

        # Toggle OFF if already bound to this app
        if current == app_name:
            print(f"Channel {channel_index} unbound from {app_name}")
            self.slider_app[channel_index] = None
            self.pw.set_volume(app_name, 1.0)
            self._update_led_all()
            return

        if not app_name:
            print("Bind failed: no app")
            return

        # Remove from any other channel
        for i in range(self.num_channels):
            if i != channel_index and self.slider_app[i] == app_name:
                self.slider_app[i] = None
                self._update_led(i)

        self.slider_app[channel_index] = app_name
        print(f"Channel {channel_index} bound to {app_name}")
        self._apply_channel_volume(channel_index)
        self._update_led_all()

    # ---------------- Auto-Unbind ----------------
    def _auto_unbind_loop(self):
        while True:
            apps = self.pw.get_apps()
            for i, app_name in enumerate(self.slider_app):
                if app_name and app_name not in apps:
                    print(f"Channel {i} auto-unbinding {app_name} (app closed)")
                    self.slider_app[i] = None
                    self.pw.set_volume(app_name, 1.0)
                    self._update_led(i)
            time.sleep(1.0)

    # ---------------- Mute ----------------
    def toggle_mute(self, channel_index):
        self.slider_muted[channel_index] = not self.slider_muted[channel_index]
        print(f"Channel {channel_index} mute={self.slider_muted[channel_index]}")
        self._apply_channel_volume(channel_index)
        self._update_led_all()

    # ---------------- Mute All (transport REC) ----------------
    def toggle_mute_all(self):
        self._all_muted = not self._all_muted
        print(f"Mute all = {self._all_muted}")
        for i in range(self.num_channels):
            self.slider_muted[i] = self._all_muted
            self._apply_channel_volume(i)
        self._update_led_all()

    # ---------------- Solo ----------------
    def toggle_solo(self, channel_index):
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

    # ---------------- Slider ----------------
    def slider_moved(self, channel_index, midi_value):
        self.slider_values[channel_index] = midi_value / 127.0
        self._update_led(channel_index)

    # ---------------- Volume Logic ----------------
    def _apply_channel_volume(self, channel_index):
        app_name = self.slider_app[channel_index]
        if not app_name:
            return

        base_volume = self.slider_values[channel_index]

        if self.slider_muted[channel_index]:
            volume = 0.0
        elif any(self.slider_soloed):
            volume = base_volume if self.slider_soloed[channel_index] else 0.0
        else:
            volume = base_volume

        self.pw.set_volume(app_name, volume)

    def _apply_solo_state(self):
        for i in range(self.num_channels):
            self._apply_channel_volume(i)

    def _restore_all_channels(self):
        for i in range(self.num_channels):
            self._apply_channel_volume(i)

    # ---------------- LEDs ----------------
    def _send_led(self, note, state: bool):
        """Send a Note On message to set an LED on or off (Mackie mode).

        Note On 0x90 with velocity 127 = LED on, velocity 0 = LED off.
        """
        velocity = LED_ON if state else LED_OFF
        self.midi_out.send_message([0x90, note, velocity])

    def _update_led(self, channel_index):
        self._send_led(LED_REC_BASE  + channel_index, self.slider_app[channel_index] is not None)
        self._send_led(LED_SOLO_BASE + channel_index, self.slider_soloed[channel_index])
        self._send_led(LED_MUTE_BASE + channel_index, self.slider_muted[channel_index])

    def _update_led_all(self):
        for i in range(self.num_channels):
            self._update_led(i)

    # ---------------- Transport helpers ----------------
    def _run(self, *cmd):
        """Fire-and-forget a subprocess."""
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _press_key(self, keycode):
        """Simulate a key press via ydotool (Wayland-compatible)."""
        self._run("ydotool", "key", str(keycode))

    # ---------------- MIDI Polling ----------------
    def _poll_midi(self):
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
                self._process_frame()

            time.sleep(0.0005)

    def _process_frame(self):
        for i in range(self.num_channels):
            self._apply_channel_volume(i)

    # ---------------- MIDI Handler ----------------
    def _handle_midi_message(self, data):
        print(f"MIDI: {data}")

        if len(data) < 3:
            return

        status, data1, data2 = data
        msg_type = status & 0xF0

        # Edge detection
        previous_value = self._last_cc_value[data1]
        self._last_cc_value[data1] = data2
        is_press = previous_value == 0 and data2 > 0

        # ------------------------------------------------
        # Pitch bend → sliders
        # ------------------------------------------------
        if msg_type == 0xE0:
            channel = status & 0x0F
            value = data1 | (data2 << 7)
            normalized = value / 16383.0
            if channel < self.num_channels:
                self.slider_values[channel] = normalized
                print(f"Slider {channel} = {normalized:.2f}")

        # ------------------------------------------------
        # Note On → channel buttons (R / S / M)
        # ------------------------------------------------
        elif msg_type == 0x90:
            if data2 == 0:
                return
            note = data1

            # R → bind to focused app
            if 0 <= note <= 7:
                channel_index = note
                focused_app = self.pw.get_focused_app()
                print("Apps:", self.pw.get_apps())
                print("Focused:", focused_app)
                if not focused_app:
                    focused_app = self.pw.get_best_app()
                    print("Fallback app:", focused_app)
                if not focused_app:
                    print("No app available to bind")
                    return
                self.bind_slider_to_app(channel_index, focused_app)

            # S → solo
            elif 8 <= note <= 15:
                self.toggle_solo(note - 8)

            # M → mute
            elif 16 <= note <= 23:
                self.toggle_mute(note - 16)

            # ------------------------------------------------
            # Transport buttons (all Note On in Mackie mode)
            # Only fire on press (data2 > 0), ignore release
            # ------------------------------------------------
            elif note == NOTE_PLAY:
                print("Transport: play-pause")
                self._run("playerctl", "play-pause")

            elif note == NOTE_STOP:
                print("Transport: stop")
                self._run("playerctl", "stop")

            elif note == NOTE_REC:
                print("Transport: mute all toggle")
                self.toggle_mute_all()

            elif note == NOTE_REW:
                print("Transport: left arrow")
                self._press_key(KEY_LEFT)

            elif note == NOTE_FF:
                print("Transport: right arrow")
                self._press_key(KEY_RIGHT)

            elif note == NOTE_PREV_TRACK:
                print("Transport: previous track")
                self._run("playerctl", "previous")

            elif note == NOTE_NEXT_TRACK:
                print("Transport: next track")
                self._run("playerctl", "next")

            # NOTE_CYCLE → unassigned for now
            elif note == NOTE_CYCLE:
                pass
