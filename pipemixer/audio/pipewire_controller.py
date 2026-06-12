"""
pipewire_controller.py

Uses pulsectl (libpulse bindings) instead of pactl subprocesses for
volume control and sink input enumeration. wpctl is still used for
app discovery via the node name list, and pw-dump for PID matching.

pulsectl works through pipewire-pulse (PipeWire's PulseAudio compatibility
layer), so this is still talking to PipeWire under the hood.
"""

import json
import os
import queue
import subprocess
import threading
import time

import difflib
import psutil
import pulsectl

from pipemixer.system.focus_detector import (
    FocusStrategy,
    detect_strategy,
    get_focused_pid_and_name,
    get_strategy_description,
    save_config,
    GNOME_EXTENSION_MSG,
    NO_STRATEGY_MSG,
)


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

        # Note: we do NOT keep a persistent pulsectl connection because
        # pulsectl does not support calling sink_input_list() or volume
        # operations from background threads while an event loop is running.
        # Instead we open a short-lived connection per operation using
        # _pulse_conn() as a context manager. This is thread-safe.

        self.focus_strategy = self._init_focus_strategy()
        self._overrides = self._load_overrides()

        # Queue for signalling the event loop to re-apply volumes
        # when new sink inputs appear
        self._event_queue: queue.Queue = queue.Queue()

        # Refresh node cache once at startup
        self._refresh_nodes()

        # Start background threads
        threading.Thread(target=self._node_refresh_loop,  daemon=True).start()
        threading.Thread(target=self._pulse_event_loop,   daemon=True).start()
        threading.Thread(target=self._volume_apply_loop,  daemon=True).start()

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
        strategy = self.focus_strategy

        if strategy == FocusStrategy.BEST_APP:
            return self.get_best_app()

        if strategy == FocusStrategy.NONE:
            return None

        try:
            pid, window_name = get_focused_pid_and_name(strategy)

            if pid:
                match = self._match_pid_in_pipewire(pid)
                if match:
                    return match
                match = self._match_process_tree_in_pipewire(pid)
                if match:
                    return match

            if window_name:
                # Check manual overrides first (window class -> pipewire name)
                override = self._overrides.get(window_name.lower())
                if override:
                    print(f"Override match: '{window_name}' -> '{override}'")
                    return override

                match = self._match_name(window_name)
                if match:
                    return match

            return None

        except Exception as e:
            print(f"Focus detection error: {e}")
            return None

    IGNORE_APPS = {
        "pipewire", "wireplumber", "kwin_wayland", "plasmashell",
        "xdg-desktop-portal", "libcanberra", "wpctl", "audio-src",
        "pipemixer", "steam", "steam voice settings",
    }

    def get_best_app(self) -> str | None:
        candidates = [
            app for app in self.get_apps()
            if app not in self.IGNORE_APPS
            and not app.startswith("output_")
            and not app.startswith("audio/")
            and not app.startswith("input_")
            and not app.startswith("monitor_")
        ]
        return candidates[-1] if candidates else None

    def is_app_running(self, app_name: str) -> bool:
        """
        Check if an app is still running as a process, regardless of
        whether it is currently producing audio. This prevents auto-unbind
        from firing just because Firefox paused a video.

        Checks in order:
          1. PipeWire node cache (app is active and producing audio)
          2. psutil process list (app is open but may be silent)
        """
        # Check PipeWire first (fast path)
        if app_name in self.node_cache:
            return True

        # Fall back to process check
        name = app_name.lower().removesuffix(".exe")
        try:
            for proc in psutil.process_iter(["name", "exe"]):
                try:
                    proc_name = (proc.info["name"] or "").lower().removesuffix(".exe")
                    if name == proc_name or name in proc_name or proc_name in name:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        return False

    def stop(self) -> None:
        self.running = False

    def _pulse_conn(self):
        """Return a fresh pulsectl.Pulse context manager for thread-safe use."""
        return pulsectl.Pulse("pipemixer")

    # -------------------------------------------------------- Focus strategy init

    def _init_focus_strategy(self) -> FocusStrategy:
        strategy = detect_strategy()

        if strategy != FocusStrategy.NONE:
            print(f"Focus detection: {get_strategy_description(strategy)}")
            return strategy

        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        wayland = os.environ.get("WAYLAND_DISPLAY", "")

        if "gnome" in desktop and (wayland or session == "wayland"):
            print(GNOME_EXTENSION_MSG)
        else:
            print(NO_STRATEGY_MSG.format(desktop=desktop or "unknown"))

        print("Would you like to use the 'best app' fallback in the meantime? [y/N]")
        try:
            response = input().strip().lower()
        except EOFError:
            response = "n"

        if response in ("y", "yes"):
            save_config({"allow_best_app_fallback": True})
            print("Using best-app fallback.")
            return FocusStrategy.BEST_APP
        else:
            save_config({"allow_best_app_fallback": False})
            print("Focus detection disabled.")
            return FocusStrategy.NONE

    # --------------------------------------------------------- Overrides

    def _load_overrides(self) -> dict[str, str]:
        """
        Load manual window-class -> PipeWire-name mappings from
        ~/.config/pipemixer/overrides.json.

        Example file:
          {
            "steam_app_359320": "elitedangerous64",
            "my_window_class":  "my_pipewire_app_name"
          }

        Keys are window class names as returned by kdotool/xdotool.
        Values are PipeWire application names as shown in wpctl status
        or pactl list sink-inputs.
        """
        path = os.path.expanduser("~/.config/pipemixer/overrides.json")
        try:
            with open(path) as f:
                overrides = json.load(f)
            print(f"Loaded {len(overrides)} override(s) from {path}")
            return {k.lower(): v.lower() for k, v in overrides.items()}
        except FileNotFoundError:
            return {}
        except Exception as e:
            print(f"Failed to load overrides: {e}")
            return {}

    # --------------------------------------------------------- Focus detection helpers

    def _get_pw_dump(self) -> list[dict]:
        try:
            result = subprocess.run(
                ["pw-dump"], capture_output=True, text=True, timeout=5,
            )
            return json.loads(result.stdout)
        except Exception:
            return []

    def _get_process_tree(self, pid: str) -> set[str]:
        pids = {pid}
        try:
            current = pid
            ancestors = []
            for _ in range(10):
                result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", current],
                    capture_output=True, text=True,
                )
                ppid = result.stdout.strip()
                if not ppid or ppid == "0" or ppid == current:
                    break
                name_result = subprocess.run(
                    ["ps", "-o", "comm=", "-p", ppid],
                    capture_output=True, text=True,
                )
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
                result = subprocess.run(
                    ["ps", "--ppid", target_pid, "-o", "pid="],
                    capture_output=True, text=True,
                )
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
            name = (
                props.get("application.name")
                or props.get("node.name")
                or props.get("application.process.binary")
            )
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
            name = (
                props.get("application.name")
                or props.get("node.name")
                or props.get("application.process.binary")
            )
            if name and obj_id:
                client_app[obj_id] = name.lower()
        if not client_app:
            return None
        # Match against pulsectl sink inputs via client index
        try:
            with self._pulse_conn() as pulse:
                for si in pulse.sink_input_list():
                    # Use proplist client.id (PipeWire object ID) not si.client
                    # (PulseAudio client index) — these are different numbers
                    client_id = si.proplist.get("client.id", "")
                    if client_id in client_app:
                        app_name = client_app[client_id]
                        if app_name not in ("pipewire", "audio-src", "wireplumber"):
                            print(f"Process tree match: {app_name}")
                            return app_name
        except Exception:
            pass
        return None

    # Minimum score (0-100) required to consider a match valid.
    # Below this threshold we assume it is a false positive.
    MATCH_THRESHOLD = 50

    def _score_match(self, a: str, b: str) -> int:
        """
        Score how well two app name strings match (0-100).

        Scoring tiers:
          100 - exact match after normalisation (.exe stripped, lowercased)
           80 - very high combined sequence + length similarity (>= 0.85)
           50 - significant combined similarity (>= 0.70, at threshold)
           30 - partial similarity (>= 0.45, below threshold)
            0 - no meaningful match

        Guards:
          - If one string is the other with only digits/punctuation appended
            (e.g. "balatro" vs "balatro2"), score is 0 — treat as different app.
          - Length ratio is multiplied into the score to prevent short strings
            matching much longer ones (e.g. "fire" vs "firefox").
        """
        def norm(s: str) -> str:
            return s.removesuffix(".exe").strip().lower()

        na, nb = norm(a), norm(b)

        if na == nb:
            return 100

        # Digit/punctuation suffix guard:
        # "balatro" vs "balatro2" should score 0, not match
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        suffix = longer[len(shorter):]
        if longer.startswith(shorter) and re.match(r'^[\d\s\-_\.]+$', suffix):
            return 0

        # Combined score: sequence similarity * length ratio
        # Both must be high for a confident match
        len_ratio = min(len(na), len(nb)) / max(len(na), len(nb)) if max(len(na), len(nb)) > 0 else 0
        ratio = difflib.SequenceMatcher(None, na, nb).ratio()
        combined = ratio * len_ratio

        if combined >= 0.85:
            return 80
        if combined >= 0.70:
            return 50
        if combined >= 0.45:
            return 30

        return 0

    def _best_match(self, window_name: str, candidates: list[str]) -> tuple[str | None, int]:
        """
        Return (best_candidate, score) for window_name from candidates.
        Returns (None, 0) if no candidate scores at or above MATCH_THRESHOLD.
        """
        if not candidates or not window_name:
            return None, 0

        best_name  = None
        best_score = 0

        for candidate in candidates:
            score = self._score_match(window_name, candidate)
            if score > best_score:
                best_score = score
                best_name  = candidate

        if best_score >= self.MATCH_THRESHOLD:
            return best_name, best_score

        return None, best_score

    def _match_name(self, window_name: str) -> str | None:
        """
        Match a window class/name against PipeWire apps and pactl sink input
        names using scored fuzzy matching with difflib.SequenceMatcher.

        Tries wpctl node names first, then pulsectl sink input proplist names.
        Returns the best match at or above MATCH_THRESHOLD, or None.
        """
        pipewire_apps = self.get_apps()

        # Try wpctl node names
        match, score = self._best_match(window_name, pipewire_apps)
        if match:
            print(f"Name match: '{window_name}' -> '{match}' (score {score})")
            return match

        # Try pulsectl sink input proplist names
        try:
            with self._pulse_conn() as pulse:
                sink_names = []
                for si in pulse.sink_input_list():
                    for field in ("application.name", "node.name"):
                        val = si.proplist.get(field, "")
                        if val:
                            sink_names.append(val)

                match, score = self._best_match(window_name, sink_names)
                if match:
                    print(f"Sink match: '{window_name}' -> '{match}' (score {score})")
                    return match.lower()
        except Exception:
            pass

        print(f"No match for '{window_name}' (all below threshold {self.MATCH_THRESHOLD})")
        return None

    # ---------------------------------------------------------- Node discovery
    # Still uses wpctl for app name discovery — it gives us the clean
    # normalised name list that we use to populate the cache.

    def _refresh_nodes(self) -> None:
        result = subprocess.run(
            ["wpctl", "status"], capture_output=True, text=True,
        )
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
    # All pactl subprocesses replaced with pulsectl API calls.

    def _find_sink_inputs(self, app_name: str) -> list:
        """
        Return all pulsectl PulseSinkInputInfo objects matching app_name.

        Matching strategy (same logic as before, but using proplist dicts
        instead of parsing pactl text output):
          Pass 1 — direct proplist name match
          Pass 2 — wpctl node ID -> pulsectl client index (handles Spotify)
        """
        name = app_name.lower()
        try:
            with self._pulse_conn() as pulse:
                sink_inputs = pulse.sink_input_list()
        except Exception as e:
            print(f"pulsectl sink_input_list error: {e}")
            return []

        # Pass 1: direct proplist match
        matches = []
        for si in sink_inputs:
            for field in ("application.name", "node.name", "application.process.binary"):
                if si.proplist.get(field, "").lower() == name:
                    matches.append(si)
                    break

        if matches:
            return matches

        # Pass 2: wpctl node ID -> proplist client.id (Spotify workaround)
        # Spotify's sink input has no application.name — we match via the
        # PipeWire client.id in the proplist, which matches wpctl node IDs.
        node_ids = set(self.node_cache.get(name, []))
        for si in sink_inputs:
            if si.proplist.get("client.id", "") in node_ids:
                matches.append(si)

        return matches

    def _apply_volume_by_name(self, app_name: str, volume: float) -> None:
        """Set volume on all matching sink inputs via pulsectl."""
        sink_inputs = self._find_sink_inputs(app_name)
        if not sink_inputs:
            return

        try:
            with self._pulse_conn() as pulse:
                # Re-fetch sink inputs inside the same connection to ensure
                # the objects are valid for the volume call
                fresh = pulse.sink_input_list()
                fresh_ids = {si.index for si in sink_inputs}
                for si in fresh:
                    if si.index in fresh_ids:
                        pulse.volume_set_all_chans(si, volume)
        except Exception as e:
            print(f"pulsectl volume set error for {app_name}: {e}")

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

    # --------------------------------------------------------------- Background

    def _node_refresh_loop(self) -> None:
        """
        Periodically refresh the wpctl node cache for app discovery.
        This is kept as a poll because wpctl has no event API.
        Volume application is now event-driven via _pulse_event_loop.
        """
        while self.running:
            self._refresh_nodes()
            time.sleep(self.poll_interval)

    def _pulse_event_loop(self) -> None:
        """
        Listen for PulseAudio/PipeWire sink input events via pulsectl.
        When a new sink input appears, signal _volume_apply_loop to
        re-apply all stored volumes — this handles apps that start
        playing audio after we bound them.
        """
        while self.running:
            try:
                with pulsectl.Pulse("pipemixer-events") as pulse:
                    def on_event(e):
                        if e.facility == pulsectl.PulseEventFacilityEnum.sink_input:
                            if e.t in (
                                pulsectl.PulseEventTypeEnum.new,
                                pulsectl.PulseEventTypeEnum.remove,
                            ):
                                # Non-blocking put — drop if queue is full
                                try:
                                    self._event_queue.put_nowait("sink_changed")
                                except queue.Full:
                                    pass

                    pulse.event_mask_set(pulsectl.PulseEventMaskEnum.sink_input)
                    pulse.event_callback_set(on_event)
                    pulse.event_listen(timeout=5.0)  # returns every 5s even if no events

            except Exception as e:
                if self.running:
                    print(f"Pulse event loop error: {e}, restarting in 2s...")
                    time.sleep(2.0)

    def _volume_apply_loop(self) -> None:
        """
        Re-apply stored volumes whenever the event loop signals a change,
        or every 2 seconds as a safety net in case an event was missed.
        """
        while self.running:
            try:
                # Wait up to 2s for an event, then apply anyway as safety net
                self._event_queue.get(timeout=2.0)
            except queue.Empty:
                pass  # Timeout — apply volumes as safety net

            if not self.running:
                break

            for app_name, volume in list(self.last_values.items()):
                self._apply_volume_by_name(app_name, volume)
