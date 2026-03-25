# main.py

import time
from pipemixer.midi.midi_engine import MidiEngine

def main():
    print("Starting PipeMixer prototype")

    # Create MidiEngine with 8 channels
    midi = MidiEngine(num_channels=8)

    # ---------------- Example Usage ----------------
    # For now, simulate binding and slider movement
    # In real usage, you would hook these to nanoKONTROL2 events

    # Bind channel 0 to Firefox
    midi.bind_slider_to_app(0, "Firefox")

    # Bind channel 1 to Spotify
    midi.bind_slider_to_app(1, "Spotify")

    # Set initial slider values (0-127)
    midi.slider_moved(0, 64)  # 50% volume
    midi.slider_moved(1, 127) # 100% volume

    # Toggle mute on channel 1
    midi.toggle_mute(1)

    # Solo channel 0
    midi.toggle_solo(0)

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
