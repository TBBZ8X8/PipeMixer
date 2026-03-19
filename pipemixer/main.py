from pipemixer.midi.midi_engine import MidiEngine
from pipemixer.audio.pipewire_controller import PipeWireController


def main():

    print("Starting PipeMixer prototype")

    audio = PipeWireController()
    midi = MidiEngine()

    def slider1(value):

        print("Setting Firefox volume:", value)

        audio.set_volume("Firefox", value)

    midi.register_slider_callback(0, slider1)

    midi.start()


if __name__ == "__main__":
    main()
