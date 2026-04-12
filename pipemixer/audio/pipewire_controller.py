import subprocess
import time
import threading
import json


class PipeWireController:

    def __init__(self, poll_interval=0.5):
        self.node_cache = {}        # app_name -> list of node IDs
        self.last_values = {}       # app_name -> last slider value
        self.last_node_volume = {}  # node_id -> last volume

        self.poll_interval = poll_interval
        self.running = True

        threading.Thread(target=self._poll_nodes_loop, daemon=True).start()

    # ---------------- Public ----------------
    def set_volume(self, app_name, volume):
        self.last_values[app_name] = volume

        node_ids = self.node_cache.get(app_name, [])
        for node in node_ids:
            self._apply_node_volume(node, volume)

    def get_apps(self):
        """Return currently active app names"""
        self._refresh_nodes()
        return list(self.node_cache.keys())

    def get_focused_app(self):
        try:
            # Step 1: get focused window ID
            window_id = subprocess.run(
                ["kdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if not window_id:
                return None

            # Step 2: get the PID of that window
            pid = subprocess.run(
                ["kdotool", "getwindowpid", window_id],
                capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if not pid:
                return None

            # Step 3: find the PipeWire node owned by that PID
            pw = subprocess.run(
                ["pw-dump"],
                capture_output=True, text=True, timeout=5
            )
            nodes = json.loads(pw.stdout)

            for node in nodes:
                props = node.get("info", {}).get("props", {})
                node_pid = str(props.get("application.process.id", ""))
                if node_pid != pid:
                    continue

                # Try the most useful name fields in order of preference
                name = (
                    props.get("application.name") or
                    props.get("node.name") or
                    props.get("application.process.binary")
                )
                if name:
                    return name.lower()

            # PID not found in PipeWire — fall back to name matching
            print(f"PID {pid} not found in PipeWire, falling back to name matching")
            return self._get_focused_app_by_name(window_id)

        except FileNotFoundError:
            print("kdotool not found. Install with: yay -S kdotool")
            return None
        except subprocess.TimeoutExpired:
            print("pw-dump or kdotool timed out")
            return None
        except Exception as e:
            print(f"Focus detection error: {e}")
            return None

    # ---------------- Node Discovery ----------------
    def _refresh_nodes(self):
        result = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True
        )

        new_cache = {}

        for line in result.stdout.splitlines():
            line = line.strip()

            if "." not in line:
                continue

            parts = line.split(".", 1)

            if not parts[0].strip().isdigit():
                continue

            node_id = parts[0].strip()
            raw_name = parts[1].strip()

            name = raw_name

            # Remove volume info
            if "[" in name:
                name = name.split("[")[0]

            # Remove extra descriptors
            if "(" in name:
                name = name.split("(")[0]

            name = name.strip().lower()

            if not name:
                continue

            if name not in new_cache:
                new_cache[name] = []

            new_cache[name].append(node_id)

        self.node_cache = new_cache

    # ---------------- Volume ----------------
    def _apply_node_volume(self, node_id, volume):
        if node_id in self.last_node_volume:
            if abs(self.last_node_volume[node_id] - volume) < 0.01:
                return

        self.last_node_volume[node_id] = volume

        subprocess.run(
            ["wpctl", "set-volume", str(node_id), str(volume)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    # ---------------- Background ----------------
    def _poll_nodes_loop(self):
        while self.running:
            self._refresh_nodes()

            for app_name, volume in self.last_values.items():
                nodes = self.node_cache.get(app_name, [])

                for node in nodes:
                    self._apply_node_volume(node, volume)

            time.sleep(self.poll_interval)

    def stop(self):
        self.running = False

    def get_best_app(self):
        """Return the best candidate app for binding"""

        ignore = {
            "pipewire",
            "wireplumber",
            "kwin_wayland",
            "plasmashell",
            "xdg-desktop-portal",
            "libcanberra",
            "wpctl"
        }

        apps = self.get_apps()

        candidates = [
            app for app in apps
            if app not in ignore
            and not app.startswith("output_")
            and not app.startswith("audio/")
        ]

        if not candidates:
            return None

        return candidates[-1]
