"""
focus_detector.py — Cross-platform focused window detection for PipeMixer

Strategies tried in order:
  1. KDE Wayland   — kdotool
  2. X11           — xdotool
  3. Sway          — swaymsg
  4. Hyprland      — hyprctl
  5. GNOME Wayland — window-calls GNOME extension via gdbus
  6. get_best_app  — fallback, only if user consented at install time

Environment is detected once at startup and stored. The detector is
initialised by PipeWireController and called by get_focused_app().
"""

import json
import os
import subprocess
from enum import Enum, auto


class FocusStrategy(Enum):
    KDE_WAYLAND   = auto()
    X11           = auto()
    SWAY          = auto()
    HYPRLAND      = auto()
    GNOME_WAYLAND = auto()
    BEST_APP      = auto()   # fallback — user must have consented
    NONE          = auto()   # no strategy available


# ── Environment detection ─────────────────────────────────────────────────────

def detect_strategy() -> FocusStrategy:
    """
    Detect the best available focus detection strategy for the current
    desktop environment and display server.
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = os.environ.get("WAYLAND_DISPLAY", "")

    def has(cmd: str) -> bool:
        return subprocess.run(
            ["which", cmd], capture_output=True
        ).returncode == 0

    # KDE Wayland
    if "kde" in desktop and (wayland or session == "wayland"):
        if has("kdotool"):
            return FocusStrategy.KDE_WAYLAND

    # X11 (any DE)
    if session == "x11" or (not wayland and "DISPLAY" in os.environ):
        if has("xdotool"):
            return FocusStrategy.X11

    # Sway
    if "sway" in desktop or os.environ.get("SWAYSOCK"):
        if has("swaymsg"):
            return FocusStrategy.SWAY

    # Hyprland
    if "hyprland" in desktop or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        if has("hyprctl"):
            return FocusStrategy.HYPRLAND

    # GNOME Wayland — requires window-calls extension
    if "gnome" in desktop and (wayland or session == "wayland"):
        if _gnome_extension_available():
            return FocusStrategy.GNOME_WAYLAND

    # get_best_app fallback — only if user consented
    config = _load_config()
    if config.get("allow_best_app_fallback"):
        return FocusStrategy.BEST_APP

    return FocusStrategy.NONE


def _gnome_extension_available() -> bool:
    """Check if the window-calls GNOME extension is installed and active."""
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell/Extensions/Windows",
                "--method", "org.gnome.Shell.Extensions.Windows.List",
            ],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0
    except Exception:
        return False


def _load_config() -> dict:
    path = os.path.expanduser("~/.config/pipemixer/config.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data: dict) -> None:
    path = os.path.expanduser("~/.config/pipemixer/config.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        existing = _load_config()
        existing.update(data)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"Failed to save config: {e}")


# ── Strategy messages ─────────────────────────────────────────────────────────

GNOME_EXTENSION_MSG = """
PipeMixer cannot detect focused windows on GNOME Wayland without a helper
extension. This is a GNOME security restriction — no workaround exists.

You have two options:

  Option A — Install the 'window-calls' GNOME extension (recommended):
    1. Open this URL in Firefox:
       https://extensions.gnome.org/extension/4724/window-calls/
    2. Click the toggle to install it
    3. Restart PipeMixer

    This gives PipeMixer accurate focus detection — pressing R will always
    bind the correct app.

  Option B — Use 'best app' fallback:
    PipeMixer will guess which app to bind based on what's currently
    producing audio. This works most of the time but can be confused if
    multiple apps are playing audio simultaneously.
    NOTE: The R button will bind whatever app is loudest/most recent,
    not necessarily the one you have focused.
"""

NO_STRATEGY_MSG = """
PipeMixer could not detect a supported focus detection method for your
desktop environment ({desktop}).

Supported environments:
  • KDE Plasma (Wayland)  — requires: kdotool      (yay -S kdotool)
  • Any DE (X11)          — requires: xdotool      (sudo apt install xdotool)
  • Sway                  — requires: swaymsg      (built into sway)
  • Hyprland              — requires: hyprctl      (built into hyprland)
  • GNOME (Wayland)       — requires: window-calls extension
                            https://extensions.gnome.org/extension/4724/window-calls/

You can still use PipeMixer with the 'best app' fallback — see above.
"""


def get_strategy_description(strategy: FocusStrategy) -> str:
    return {
        FocusStrategy.KDE_WAYLAND:   "KDE Wayland (kdotool)",
        FocusStrategy.X11:           "X11 (xdotool)",
        FocusStrategy.SWAY:          "Sway (swaymsg)",
        FocusStrategy.HYPRLAND:      "Hyprland (hyprctl)",
        FocusStrategy.GNOME_WAYLAND: "GNOME Wayland (window-calls extension)",
        FocusStrategy.BEST_APP:      "best-app fallback (no focus detection)",
        FocusStrategy.NONE:          "none",
    }[strategy]


# ── Per-strategy PID getters ──────────────────────────────────────────────────

def get_focused_pid_and_name(strategy: FocusStrategy) -> tuple[str | None, str | None]:
    """
    Returns (pid, window_class_or_name) for the currently focused window,
    or (None, None) if detection fails.
    """
    try:
        if strategy == FocusStrategy.KDE_WAYLAND:
            return _focused_kde()
        elif strategy == FocusStrategy.X11:
            return _focused_x11()
        elif strategy == FocusStrategy.SWAY:
            return _focused_sway()
        elif strategy == FocusStrategy.HYPRLAND:
            return _focused_hyprland()
        elif strategy == FocusStrategy.GNOME_WAYLAND:
            return _focused_gnome()
        else:
            return None, None
    except Exception as e:
        print(f"Focus detection error ({strategy.name}): {e}")
        return None, None


def _focused_kde() -> tuple[str | None, str | None]:
    window_id = subprocess.run(
        ["kdotool", "getactivewindow"],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip()
    if not window_id:
        return None, None

    pid = subprocess.run(
        ["kdotool", "getwindowpid", window_id],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip()

    name = subprocess.run(
        ["kdotool", "getwindowclassname", window_id],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip().lower()

    return pid or None, name or None


def _focused_x11() -> tuple[str | None, str | None]:
    # Get focused window ID
    window_id = subprocess.run(
        ["xdotool", "getactivewindow"],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip()
    if not window_id:
        return None, None

    pid = subprocess.run(
        ["xdotool", "getwindowpid", window_id],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip()

    name = subprocess.run(
        ["xdotool", "getwindowclassname", window_id],
        capture_output=True, text=True, timeout=2,
    ).stdout.strip().lower()

    return pid or None, name or None


def _focused_sway() -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["swaymsg", "-t", "get_tree"],
        capture_output=True, text=True, timeout=2,
    )
    tree = json.loads(result.stdout)

    def find_focused(node: dict) -> dict | None:
        if node.get("focused"):
            return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            found = find_focused(child)
            if found:
                return found
        return None

    focused = find_focused(tree)
    if not focused:
        return None, None

    pid  = str(focused.get("pid", "")) or None
    name = (focused.get("app_id") or focused.get("window_properties", {}).get("class", "")).lower()
    return pid, name or None


def _focused_hyprland() -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"],
        capture_output=True, text=True, timeout=2,
    )
    data = json.loads(result.stdout)
    pid  = str(data.get("pid", "")) or None
    name = (data.get("class") or data.get("title", "")).lower()
    return pid, name or None


def _focused_gnome() -> tuple[str | None, str | None]:
    """
    Uses the window-calls GNOME extension.
    https://extensions.gnome.org/extension/4724/window-calls/
    """
    result = subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell",
            "--object-path", "/org/gnome/Shell/Extensions/Windows",
            "--method", "org.gnome.Shell.Extensions.Windows.List",
        ],
        capture_output=True, text=True, timeout=2,
    )

    # Parse the gdbus output — it returns a GVariant tuple string
    raw = result.stdout.strip()
    # Strip outer tuple wrapper: "([{...}, {...}],)"
    if raw.startswith("(") and raw.endswith(",)"):
        raw = raw[1:-2].strip()

    windows = json.loads(raw)

    focused = next((w for w in windows if w.get("focus")), None)
    if not focused:
        return None, None

    pid  = str(focused.get("pid", "")) or None
    name = (focused.get("wm_class_instance") or focused.get("wm_class", "")).lower()
    return pid, name or None
