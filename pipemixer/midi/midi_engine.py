import mido
import time


class MidiEngine:

    def __init__(self):

        self.callbacks = {}
        self.latest_values = {}
        self.last_sent_values = {}

        ports = mido.get_input_names()
        print("Available MIDI ports:", ports)

        self.port_name = None

        for p in ports:
            if "nanoKONTROL2" in p:
                self.port_name = p
                break

        if not self.port_name:
            raise RuntimeError("nanoKONTROL2 not found")

        print("Using MIDI port:", self.port_name)

    def register_slider_callback(self, control_number, callback):

        self.callbacks[control_number] = callback
        self.latest_values[control_number] = None
        self.last_sent_values[control_number] = None

    def start(self):

        with mido.open_input(self.port_name) as port:

            print("Listening for MIDI events...")

            last_tick = time.time()

            while True:

                # Read all pending MIDI messages quickly
                for msg in port.iter_pending():

                    if msg.type == "control_change":
                        if msg.control in self.callbacks:
                            self.latest_values[msg.control] = msg.value

                now = time.time()

                # Run at ~60Hz (every 16ms)
                if now - last_tick >= 0.016:

                    last_tick = now

                    for control, value in self.latest_values.items():

                        if value is None:
                            continue

                        # Only send if value changed (prevents spam)
                        if value == self.last_sent_values[control]:
                            continue

                        self.last_sent_values[control] = value

                        print(f"CC {control} -> {value}")

                        self.callbacks[control](value)

                time.sleep(0.001)
