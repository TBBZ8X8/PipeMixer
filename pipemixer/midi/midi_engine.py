import subprocess
import threading
import time
import rtmidi
from pipemixer.audio.pipewire_controller import PipeWireController

LED_REC_BASE  = 0
LED_SOLO_BASE = 8
LED_MUTE_BASE = 16
NOTE_PREV_TRACK = 46
NOTE_NEXT_TRACK = 47
NOTE_CYCLE      = 89
NOTE_REW        = 91
NOTE_FF         = 92
NOTE_STOP       = 93
NOTE_PLAY       = 94
NOTE_REC        = 95
KEY_LEFT  = 105
KEY_RIGHT = 106


class MidiEngine:

    def __init__(self, num_channels: int = 8, midi_in_port_name=None, midi_out_port_name=None):
        self.pw = PipeWireController()
        self.num_channels = num_channels
        self.slider_app    = [None]  * num_channels
        self.slider_values = [1.0]   * num_channels
        self.slider_muted  = [False] * num_channels
        self.slider_soloed = [False] * num_channels
        self._all_muted    = False
        self._last_value   = [0] * 128

        # Per-channel app history for deferred rebind.
        # Each channel remembers ALL apps ever bound to it so that
        # whichever one opens first gets auto-rebound.
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

    def _open_midi_ports(self, in_name, out_name) -> None:
        in_ports  = self.midi_in.get_ports()
        out_ports = self.midi_out.get_ports()
        print("\nAvailable MIDI INPUT ports:")
        for i, name in enumerate(in_ports):
            print(f"  [{i}] {name}")
        print("Available MIDI OUTPUT ports:")
        for i, name in enumerate(out_ports):
            print(f"  [{i}] {name}")
        in_idx  = next((i for i, n in enumerate(in_ports)  if "nanoKONTROL" in n), 1)
        out_idx = next((i for i, n in enumerate(out_ports) if "nanoKONTROL" in n), 1)
        self.midi_in.open_port(in_idx)
        self.midi_out.open_port(out_idx)
        print(f"Opened MIDI input:  {in_ports[in_idx]}")
        print(f"Opened MIDI output: {out_ports[out_idx]}")

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

    def bind_slider_to_app(self, channel_index: int, app_name: str) -> None:
        current = self.slider_app[channel_index]

        if current == app_name:
            print(f"Channel {channel_index} unbound from {app_name}")
            self.slider_app[channel_index] = None
            self.pw.set_volume(app_name, 1.0)
            self._update_led_all()
            return

        if not app_name:
            print("Bind failed: no app")
            return

        # BUG FIX: Remove app from any other channel's active slot AND history.
        # Ensures the app only auto-rebinds to this channel going forward.
        for i in range(self.num_channels):
            if i == channel_index:
                continue
            if self.slider_app[i] == app_name:
                self.slider_app[i] = None
                self._update_led(i)
            if app_name in self.slider_app_history[i]:
                self.slider_app_history[i].remove(app_name)

        if app_name not in self.slider_app_history[channel_index]:
            self.slider_app_history[channel_index].append(app_name)

        self.slider_app[channel_index] = app_name
        print(f"Channel {channel_index} bound to {app_name}")
        self._apply_channel_volume(channel_index)
        self._save_bindings()
        self._update_led_all()

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

    def _deferred_rebind_loop(self) -> None:
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

    def slider_moved(self, channel_index: int, midi_value: int) -> None:
        self.slider_values[channel_index] = midi_value / 127.0
        self._update_led(channel_index)

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

    def _send_led(self, note: int, state: bool) -> None:
        self.midi_out.send_message([0x90, note, 127 if state else 0])

    def _update_led(self, channel_index: int) -> None:
        self._send_led(LED_REC_BASE  + channel_index, self.slider_app[channel_index] is not None)
        self._send_led(LED_SOLO_BASE + channel_index, self.slider_soloed[channel_index])
        self._send_led(LED_MUTE_BASE + channel_index, self.slider_muted[channel_index])

    def _update_led_all(self) -> None:
        for i in range(self.num_channels):
            self._update_led(i)

    def _run(self, *cmd: str) -> None:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _press_key(self, keycode: int) -> None:
        self._run("ydotool", "key", str(keycode))

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

    def _handle_midi_message(self, data: list[int]) -> None:
        if len(data) < 3:
            return
        status, data1, data2 = data
        msg_type = status & 0xF0
        self._last_value[data1] = data2
        if msg_type == 0xE0:
            ch = status & 0x0F
            if ch < self.num_channels:
                self.slider_values[ch] = (data1 | (data2 << 7)) / 16383.0
        elif msg_type == 0x90:
            if data2 == 0:
                return
            note = data1
            if   0 <= note <= 7:          self._handle_bind(note)
            elif 8 <= note <= 15:         self.toggle_solo(note - 8)
            elif 16 <= note <= 23:        self.toggle_mute(note - 16)
            elif note == NOTE_PLAY:       self._run("playerctl", "play-pause")
            elif note == NOTE_STOP:       self._run("playerctl", "stop")
            elif note == NOTE_REC:        self.toggle_mute_all()
            elif note == NOTE_REW:        self._press_key(KEY_LEFT)
            elif note == NOTE_FF:         self._press_key(KEY_RIGHT)
            elif note == NOTE_PREV_TRACK: self._run("playerctl", "previous")
            elif note == NOTE_NEXT_TRACK: self._run("playerctl", "next")

    def _handle_bind(self, channel_index: int) -> None:
        focused_app = self.pw.get_focused_app()
        if not focused_app:
            focused_app = self.pw.get_best_app()
        if not focused_app:
            print("No app available to bind")
            return
        self.bind_slider_to_app(channel_index, focused_app)
