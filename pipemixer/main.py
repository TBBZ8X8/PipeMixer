import signal
import time

from pipemixer.midi.midi_engine import MidiEngine


def main() -> None:
    print("Starting PipeMixer")

    midi = MidiEngine(num_channels=8)

    def _shutdown(sig, frame):
        print("\nStopping PipeMixer")
        midi.pw.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
