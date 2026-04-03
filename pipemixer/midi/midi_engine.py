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
        self.slider_values = [1] * num_channels
        self.slider_muted = [False] * num_channels
        self.slider_soloed = [False] * num_channels

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

        # Force clean startup state
        self.slider_muted = [False] * self.num_channels
        self.slider_soloed = [False] * self.num_channels
        self.slider_app = [None] * self.num_channels

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
        self._update_led_all()
    # ---------------- Mute ----------------
    def toggle_mute(self, channel_index):
        self.slider_muted[channel_index] = not self.slider_muted[channel_index]
        print(f"Channel {channel_index} mute={self.slider_muted[channel_index]}")
        self._apply_channel_volume(channel_index)
        self._update_led_all()

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
        # Notes based on Mackie layout
        rec_note = channel_index          # R
        solo_note = channel_index + 8     # S
        mute_note  = channel_index + 16    # M

        def send(note, state):
            velocity = 127 if state else 0
            self.midi_out.send_message([0x90, note, velocity])

        send(rec_note, self.slider_app[channel_index] is not None)
        send(solo_note, self.slider_soloed[channel_index])
        send(mute_note, self.slider_muted[channel_index])

    def _update_led_all(self):
        for i in range(self.num_channels):
            self._update_led(i)

    # ---------------- MIDI Polling ----------------
    def _poll_midi(self):
        last_tick = time.time()

        while True:
            # Drain all pending messages
            while True:
                msg = self.midi_in.get_message()
                if not msg:
                    break

                data, _ = msg
                self._handle_midi_message(data)

            now = time.time()

            # ~60Hz processing loop
            if now - last_tick >= 0.016:
                last_tick = now
                self._process_frame()

            time.sleep(0.0005)

    def _process_frame(self):
            for i in range(self.num_channels):
                self._apply_channel_volume(i)

    def _handle_midi_message(self, data):
        print(f"MIDI: {data}")

        if len(data) < 3:
            return

        status = data[0]
        data1 = data[1]
        data2 = data[2]

        msg_type = status & 0xF0
        channel = status & 0x0F

        # ---------------- 🎚 Sliders (Pitch Bend) ----------------
        if msg_type == 0xE0:
            # Pitch bend is 14-bit: combine LSB + MSB
            value = data1 | (data2 << 7)
            normalized = value / 16383.0

            # In Mackie mode, each channel uses a different MIDI channel
            channel_index = channel

            if channel_index < self.num_channels:
                self.slider_values[channel_index] = normalized
                print(f"Slider {channel_index} = {normalized:.2f}")

        # ---------------- 🔘 Buttons (Note On/Off) ----------------
        elif msg_type == 0x90:  # Note On
            note = data1
            velocity = data2

            if velocity == 0:
                return  # treat as Note Off

            # ---- Mapping (typical Mackie layout) ----
            # These may vary slightly — we’ll refine if needed

            # R button → bind
            if 0 <= note <= 7:
                channel_index = note
                focused_app = "Firefox"  # placeholder
                self.bind_slider_to_app(channel_index, focused_app)

            # S button → solo
            elif 8 <= note <= 15:
                channel_index = note - 8
                self.toggle_solo(channel_index)

            # M button → mute
            elif 16 <= note <= 23:
                channel_index = note - 16
                self.toggle_mute(channel_index)
