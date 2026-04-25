import json
import os
import subprocess
import threading
import time


def _get_save_path() -> str:
    path = os.path.expanduser("~/.config/pipemixer/bindings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


class PipeWireController:

    def __init__(self, poll_interval: float = 0.5):
        self.last_values: dict[str, float] = {}
        self.node_cache: dict[str, list[str]] = {}
        self.poll_interval = poll_interval
        self.running = True
        threading.Thread(target=self._poll_nodes_loop, daemon=True).start()

    # ------------------------------------------------------------------ Public

    def set_volume(self, app_name: str, volume: float) -> None:
        if abs(self.last_values.get(app_name, -1) - volume) < 0.01:
            return
        self.last_values[app_name] = volume
        self._apply_volume_by_name(app_name, volume)

    def get_apps(self) -> list[str]:
        self._refresh_nodes()
        return list(self.node_cache.keys())

    def get_focused_app(self) -> str | None:
        try:
            window_id = subprocess.run(
                ["kdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if not window_id:
                return None
            pid = subprocess.run(
                ["kdotool", "getwindowpid", window_id],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if pid:
                match = self._match_pid_in_pipewire(pid)
                if match:
                    return match
                match = self._match_process_tree_in_pipewire(pid)
                if match:
                    return match
            return self._get_focused_app_by_name(window_id)
        except FileNotFoundError:
            print("kdotool not found — install with: yay -S kdotool")
            return None
        except subprocess.TimeoutExpired:
            print("kdotool or pw-dump timed out")
            return None
        except Exception as e:
            print(f"Focus detection error: {e}")
            return None

    def get_best_app(self) -> str | None:
        ignore = {
            "pipewire", "wireplumber", "kwin_wayland", "plasmashell",
            "xdg-desktop-portal", "libcanberra", "wpctl", "audio-src",
        }
        candidates = [
            app for app in self.get_apps()
            if app not in ignore
            and not app.startswith("output_")
            and not app.startswith("audio/")
        ]
        return candidates[-1] if candidates else None

    def stop(self) -> None:
        self.running = False

    # -------------------------------------------------------- Binding persistence

    def save_bindings(self, slider_app_history: list[list[str]]) -> None:
        try:
            with open(_get_save_path(), "w") as f:
                json.dump({"bindings": slider_app_history}, f, indent=2)
        except Exception as e:
            print(f"Failed to save bindings: {e}")

    def load_bindings(self, num_channels: int) -> list[list[str]]:
        try:
            with open(_get_save_path()) as f:
                data = json.load(f)
            raw = data.get("bindings", [])
            result: list[list[str]] = []
            for entry in raw:
                if isinstance(entry, list):
                    result.append(entry)
                elif isinstance(entry, str):
                    result.append([entry])
                else:
                    result.append([])
            while len(result) < num_channels:
                result.append([])
            return result[:num_channels]
        except FileNotFoundError:
            return [[] for _ in range(num_channels)]
        except Exception as e:
            print(f"Failed to load bindings: {e}")
            return [[] for _ in range(num_channels)]

    # --------------------------------------------------------- Focus detection

    def _get_pw_dump(self) -> list[dict]:
        try:
            result = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5)
            return json.loads(result.stdout)
        except Exception:
            return []

    def _get_process_tree(self, pid: str) -> set[str]:
        pids = {pid}
        try:
            current = pid
            ancestors = []
            for _ in range(10):
                result = subprocess.run(["ps", "-o", "ppid=", "-p", current], capture_output=True, text=True)
                ppid = result.stdout.strip()
                if not ppid or ppid == "0" or ppid == current:
                    break
                name_result = subprocess.run(["ps", "-o", "comm=", "-p", ppid], capture_output=True, text=True)
                name = name_result.stdout.strip().lower()
                ancestors.append((ppid, name))
                if "steam" in name or ppid == "1":
                    break
                current = ppid
            root_pid = pid
            for ppid, name in ancestors[:3]:
                if "steam" in name:
                    root_pid = ppid
                    break
                root_pid = ppid
            for target_pid in (root_pid, pid):
                result = subprocess.run(["ps", "--ppid", target_pid, "-o", "pid="], capture_output=True, text=True)
                for p in result.stdout.split():
                    pids.add(p.strip())
        except Exception:
            pass
        return pids

    def _match_pid_in_pipewire(self, pid: str) -> str | None:
        for node in self._get_pw_dump():
            props = node.get("info", {}).get("props", {})
            if str(props.get("application.process.id", "")) != pid:
                continue
            name = (props.get("application.name") or props.get("node.name") or props.get("application.process.binary"))
            if name:
                return name.lower()
        return None

    def _match_process_tree_in_pipewire(self, pid: str) -> str | None:
        tree_pids = self._get_process_tree(pid)
        client_app: dict[str, str] = {}
        for node in self._get_pw_dump():
            props = node.get("info", {}).get("props", {})
            node_pid = str(props.get("application.process.id", ""))
            if node_pid not in tree_pids:
                continue
            obj_id = str(node.get("id", ""))
            name = (props.get("application.name") or props.get("node.name") or props.get("application.process.binary"))
            if name and obj_id:
                client_app[obj_id] = name.lower()
        if not client_app:
            return None
        for inp in self._get_sink_inputs():
            client_id = inp.get("client.id", "")
            if client_id in client_app:
                app_name = client_app[client_id]
                if app_name not in ("pipewire", "audio-src", "wireplumber"):
                    print(f"Process tree match: {app_name}")
                    return app_name
        return None

    def _get_focused_app_by_name(self, window_id: str) -> str | None:
        try:
            class_name = subprocess.run(["kdotool", "getwindowclassname", window_id], capture_output=True, text=True, timeout=2).stdout.strip().lower()
            window_title = subprocess.run(["kdotool", "getwindowname", window_id], capture_output=True, text=True, timeout=2).stdout.strip().lower()
        except Exception:
            return None
        pipewire_apps = self.get_apps()
        def normalize(name: str) -> str:
            return name.removesuffix(".exe").strip().lower()
        for candidate in (class_name, window_title):
            if candidate in pipewire_apps:
                return candidate
        for app in pipewire_apps:
            norm = normalize(app)
            if norm == normalize(class_name) or norm == normalize(window_title):
                return app
        for app in pipewire_apps:
            norm = normalize(app)
            for candidate in (normalize(class_name), normalize(window_title)):
                if candidate and (candidate in norm or norm in candidate):
                    return app
        for inp in self._get_sink_inputs():
            for field in ("application.name", "node.name"):
                val = normalize(inp.get(field, ""))
                if not val:
                    continue
                for candidate in (normalize(class_name), normalize(window_title)):
                    if candidate and (candidate in val or val in candidate):
                        return inp.get(field, "").lower()
        print(f"No PipeWire match for '{class_name}' / '{window_title}'")
        return None

    # ---------------------------------------------------------- Node discovery

    def _refresh_nodes(self) -> None:
        result = subprocess.run(["wpctl", "status"], capture_output=True, text=True)
        new_cache: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if "." not in line:
                continue
            parts = line.split(".", 1)
            if not parts[0].strip().isdigit():
                continue
            node_id = parts[0].strip()
            name = parts[1].strip()
            if "[" in name:
                name = name.split("[")[0]
            if "(" in name:
                name = name.split("(")[0]
            name = name.strip().lower()
            if not name:
                continue
            new_cache.setdefault(name, []).append(node_id)
        self.node_cache = new_cache

    # --------------------------------------------------------------- Volume

    def _get_sink_inputs(self) -> list[dict]:
        CAPTURE_KEYS = {"application.name", "node.name", "application.process.binary", "client.id"}
        result = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True)
        inputs: list[dict] = []
        current: dict = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Sink Input #"):
                if current:
                    inputs.append(current)
                current = {"id": line.split("#")[1]}
            elif "=" in line and current:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').lower()
                if key in CAPTURE_KEYS:
                    current[key] = value
        if current:
            inputs.append(current)
        return inputs

    def _find_all_sink_input_ids(self, app_name: str) -> list[str]:
        name = app_name.lower()
        sink_inputs = self._get_sink_inputs()
        matches = []
        for inp in sink_inputs:
            for field in ("application.name", "node.name", "application.process.binary"):
                if inp.get(field, "") == name:
                    matches.append(inp["id"])
                    break
        if matches:
            return matches
        node_ids = set(self.node_cache.get(name, []))
        for inp in sink_inputs:
            if inp.get("client.id") in node_ids:
                matches.append(inp["id"])
        return matches

    def _apply_volume_by_name(self, app_name: str, volume: float) -> None:
        sink_ids = self._find_all_sink_input_ids(app_name)
        if not sink_ids:
            return
        percent = f"{int(volume * 100)}%"
        for sink_id in sink_ids:
            subprocess.run(["pactl", "set-sink-input-volume", sink_id, percent], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --------------------------------------------------------------- Background

    def _poll_nodes_loop(self) -> None:
        while self.running:
            self._refresh_nodes()
            for app_name, volume in list(self.last_values.items()):
                self._apply_volume_by_name(app_name, volume)
            time.sleep(self.poll_interval)
