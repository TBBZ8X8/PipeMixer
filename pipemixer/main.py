# main.py

import time
from pipemixer.midi.midi_engine import MidiEngine

def main():
    print("Starting PipeMixer prototype")

    # Create MidiEngine with 8 channels
    midi = MidiEngine(num_channels=8)

    # Run indefinitely
    try:
        while True:
            time.sleep(1)
            # Here you would normally poll MIDI input
            # For example:
            # midi.slider_moved(channel_index, midi_value)
            # midi.toggle_mute(channel_index)
            # midi.toggle_solo(channel_index)
            # midi.bind_slider_to_app(channel_index, focused_app)
    except KeyboardInterrupt:
        print("Stopping PipeMixer")
        midi.pw.running = False

if __name__ == "__main__":
    main()
