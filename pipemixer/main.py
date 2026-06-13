import signal
import sys
import time

from pipemixer.midi.midi_engine import MidiEngine

__version__ = "0.5.0"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-v"):
        print(f"PipeMixer v{__version__}")
        return

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
