import time
import threading
import rtmidi
from pipemixer.audio.pipewire_controller import PipeWireController


class MidiEngine:
    def __init__(self, num_channels=8, midi_in_port_name=None, midi_out_port_name=None):
        self.pw = PipeWireController()
        self.num_channels = num_channels

        # ---------------- Channel state ----------------
        self.slider_app = [None] * num_channels
        self.slider_values = [0.5] * num_channels
        self.slider_muted = [False] * num_channels
        self.slider_soloed = [False] * num_channels

        # ---------------- Startup protection ----------------
        self._startup_ignore_messages = 20

        # ---------------- Edge detection ----------------
        self._last_cc_value = [0] * 128

        # ---------------- MIDI Setup ----------------
        self.midi_in = rtmidi.MidiIn()
        self.midi_out = rtmidi.MidiOut()

        self._open_midi_ports(midi_in_port_name, midi_out_port_name)

        # Initialize LEDs to known state
        self._update_led_all()

        # Start MIDI polling
        threading.Thread(target=self._poll_midi, daemon=True).start()

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
        print(f"Opened MIDI input: {self.midi_in.get_ports()[1]}")

        self.midi_out.open_port(1)
        print(f"Opened MIDI output: {self.midi_out.get_ports()[1]}")
    # ---------------- Bind (R) ----------------
    def bind_slider_to_app(self, channel_index, app_name):
        # Unbind app from any other channel
        for i in range(self.num_channels):
            if i != channel_index and self.slider_app[i] == app_name:
                self.slider_app[i] = None
                self._update_led(i)

        # Bind app to this channel
        self.slider_app[channel_index] = app_name
        print(f"Channel {channel_index} bound to {app_name}")
        self._apply_channel_volume(channel_index)
        self._update_led(channel_index)
    # ---------------- Mute ----------------
    def toggle_mute(self, channel_index):
        self.slider_muted[channel_index] = not self.slider_muted[channel_index]
        print(f"Channel {channel_index} mute={self.slider_muted[channel_index]}")
        self._apply_channel_volume(channel_index)
        self._update_led(channel_index)

    # ---------------- Solo (Mutually Exclusive) ----------------
    def toggle_solo(self, channel_index):
        if self.slider_soloed[channel_index]:
            # Clear all solos
            self.slider_soloed = [False] * self.num_channels
            self._restore_all_channels()
            print(f"Channel {channel_index} solo cleared")
        else:
            # Set only this channel
            self.slider_soloed = [False] * self.num_channels
            self.slider_soloed[channel_index] = True
            self._apply_solo_state()
            print(f"Channel {channel_index} solo active")

        self._update_led_all()

    # ---------------- Slider ----------------
    def slider_moved(self, channel_index, midi_value):
        self.slider_values[channel_index] = midi_value / 127.0
        self._apply_channel_volume(channel_index)
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
            if self.slider_soloed[channel_index]:
                volume = base_volume
            else:
                volume = 0.0
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
    def _update_led(self, channel_index):
        r_on = 127 if self.slider_app[channel_index] else 0
        m_on = 127 if self.slider_muted[channel_index] else 0
        s_on = 127 if self.slider_soloed[channel_index] else 0

        base_cc = 20 + channel_index * 3
        self.midi_out.send_message([0xB0, base_cc, r_on])
        self.midi_out.send_message([0xB0, base_cc + 1, m_on])
        self.midi_out.send_message([0xB0, base_cc + 2, s_on])

    def _update_led_all(self):
        for i in range(self.num_channels):
            self._update_led(i)

    # ---------------- MIDI Polling ----------------
    def _poll_midi(self):
        while True:
            msg = self.midi_in.get_message()
            if msg:
                data, _ = msg
                self._handle_midi_message(data)
            time.sleep(0.001)

    def _handle_midi_message(self, data):
        print(f"MIDI: {data}")

        if len(data) < 3:
            return

        # Drop first N MIDI messages (device initialization spam)
        if self._startup_ignore_messages > 0:
            self._startup_ignore_messages -= 1
            return

        status, note_cc, value = data[:3]

        # Edge detection
        previous_value = self._last_cc_value[note_cc]
        self._last_cc_value[note_cc] = value

        # Only trigger on rising edge (button press)
        is_press = previous_value == 0 and value > 0

        # Control Change
        if 0xB0 <= status <= 0xBF:

            # 🎚 Sliders (0–7)
            if 0 <= note_cc <= 7:
                channel_index = note_cc
                self.slider_moved(channel_index, value)

            # 🎛 Knobs (16–23) → ignore for now
            elif 16 <= note_cc <= 23:
                return

            # 🔘 Solo (S) buttons (32–39)
            elif 32 <= note_cc <= 39 and is_press:
                channel_index = note_cc - 32
                self.toggle_solo(channel_index)

            # 🔇 Mute (M) buttons (48–55)
            elif 48 <= note_cc <= 55 and is_press:
                channel_index = note_cc - 48
                self.toggle_mute(channel_index)

            # 🔴 Record (R) buttons (64–71)
            elif 64 <= note_cc <= 71 and is_press:
                channel_index = note_cc - 64
                focused_app = "Firefox"
                self.bind_slider_to_app(channel_index, focused_app)
