#!/usr/bin/env bash
# =============================================================================
# PipeMixer — Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/TBBZ8X8/PipeMixer/main/install.sh | bash
# =============================================================================
set -e

REPO="https://github.com/TBBZ8X8/PipeMixer"
PIPEMIXER_VERSION="0.4.1"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BLUE}==>${NC} ${BOLD}$*${NC}"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✗${NC} $*"; exit 1; }

# ── Distro detection ─────────────────────────────────────────────────────────
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${ID_LIKE:-$ID}"
    elif command -v pacman &>/dev/null; then echo "arch"
    elif command -v apt   &>/dev/null; then echo "debian"
    elif command -v dnf   &>/dev/null; then echo "fedora"
    elif command -v zypper &>/dev/null; then echo "opensuse"
    else echo "unknown"
    fi
}

DISTRO=$(detect_distro)

# ── Package manager helpers ───────────────────────────────────────────────────
pkg_install() {
    case "$DISTRO" in
        *arch*)             sudo pacman -S --needed --noconfirm "$@" ;;
        *debian*|*ubuntu*)  sudo apt-get install -y "$@" ;;
        *fedora*|*rhel*)    sudo dnf install -y "$@" ;;
        *opensuse*)         sudo zypper install -y "$@" ;;
        *) warn "Unknown distro — please install manually: $*"; return 1 ;;
    esac
}

pkg_name() {
    local pkg="$1"
    case "$pkg" in
        pipx)
            case "$DISTRO" in
                *arch*)   echo "python-pipx" ;;
                *)        echo "pipx" ;;
            esac ;;
        alsa-dev)
            case "$DISTRO" in
                *arch*)     echo "alsa-lib" ;;
                *debian*)   echo "libasound2-dev" ;;
                *fedora*)   echo "alsa-lib-devel" ;;
                *opensuse*) echo "alsa-devel" ;;
                *)          echo "alsa-lib" ;;
            esac ;;
        python-dev)
            case "$DISTRO" in
                *arch*)   echo "python" ;;
                *debian*) echo "python3-dev" ;;
                *fedora*) echo "python3-devel" ;;
                *)        echo "python3" ;;
            esac ;;
        kdotool)
            case "$DISTRO" in
                *arch*)   echo "kdotool" ;;
                *)        echo "" ;;
            esac ;;
        playerctl) echo "playerctl" ;;
        ydotool)   echo "ydotool" ;;
        xdotool)   echo "xdotool" ;;
        wpctl)
            case "$DISTRO" in
                *) echo "wireplumber" ;;
            esac ;;
        git) echo "git" ;;
    esac
}

check_and_install() {
    local cmd="$1"
    local logical="$2"
    local required="${3:-false}"

    if command -v "$cmd" &>/dev/null; then
        success "$cmd found"
        return 0
    fi

    local pkg
    pkg=$(pkg_name "$logical")

    if [ -z "$pkg" ]; then
        warn "$cmd not available via package manager on this distro."
        if [ "$logical" = "kdotool" ]; then
            warn "Install kdotool manually: cargo install kdotool"
            warn "Focus detection will fall back to name matching without it."
        fi
        return 0
    fi

    if [ "$required" = "true" ]; then
        read -rp "  $cmd is required. Install $pkg? [Y/n] " response
    else
        read -rp "  $cmd recommended for full functionality. Install $pkg? [Y/n] " response
    fi

    case "$response" in
        [nN]*)
            if [ "$required" = "true" ]; then
                error "$cmd is required. Aborting."
            else
                warn "Skipping $cmd — some features may not work."
            fi
            ;;
        *)
            pkg_install "$pkg"
            success "$cmd installed"
            ;;
    esac
}

# ── Detect desktop environment ────────────────────────────────────────────────
detect_focus_tool() {
    local desktop="${XDG_CURRENT_DESKTOP:-}"
    local session="${XDG_SESSION_TYPE:-}"
    local wayland="${WAYLAND_DISPLAY:-}"

    if echo "$desktop" | grep -qi "kde" && [ -n "$wayland" ]; then
        echo "kdotool"
    elif [ "$session" = "x11" ] || [ -z "$wayland" ]; then
        echo "xdotool"
    elif echo "$desktop" | grep -qi "sway" || [ -n "$SWAYSOCK" ]; then
        echo "swaymsg"
    elif echo "$desktop" | grep -qi "hyprland" || [ -n "$HYPRLAND_INSTANCE_SIGNATURE" ]; then
        echo "hyprctl"
    elif echo "$desktop" | grep -qi "gnome"; then
        echo "gnome"
    else
        echo "unknown"
    fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  PipeMixer v${PIPEMIXER_VERSION}${NC}"
echo -e "  MIDI per-app volume mixer for Linux / PipeWire"
echo -e "  ${REPO}"
echo ""

# ── nanoKONTROL2 Mackie mode prompt ──────────────────────────────────────────
info "nanoKONTROL2 setup"
echo ""
echo "  PipeMixer requires your nanoKONTROL2 to be in Mackie Control mode."
echo "  If you haven't done this yet:"
echo ""
echo "    1. Unplug the USB cable"
echo "    2. Hold SET + MARKER ▶ simultaneously"
echo "    3. While holding both, plug the USB cable back in"
echo "    4. Release the buttons"
echo ""
read -rp "  Is your nanoKONTROL2 in Mackie mode? [y/N] " mk_response
case "$mk_response" in
    [yY]*) success "Mackie mode confirmed" ;;
    *)
        warn "Please switch to Mackie mode and re-run the installer."
        echo "  Full instructions: ${REPO}#nanokontrol2-setup"
        exit 0
        ;;
esac
echo ""

# ── Dependency checks ─────────────────────────────────────────────────────────
info "Checking dependencies..."

check_and_install python3   python-dev  true
check_and_install pipx      pipx        true
check_and_install git       git         true
check_and_install wpctl     wpctl       true
check_and_install playerctl playerctl   false
check_and_install ydotool   ydotool     false

# Focus detection tool based on DE
FOCUS_TOOL=$(detect_focus_tool)
case "$FOCUS_TOOL" in
    kdotool)
        check_and_install kdotool kdotool false ;;
    xdotool)
        check_and_install xdotool xdotool false ;;
    gnome)
        echo ""
        warn "GNOME Wayland detected."
        warn "Focus detection requires the 'window-calls' extension:"
        warn "  https://extensions.gnome.org/extension/4724/window-calls/"
        warn "Without it, PipeMixer will prompt you to use a fallback on first run."
        ;;
    swaymsg|hyprctl)
        success "Focus detection: $FOCUS_TOOL (built into your compositor)" ;;
    *)
        warn "Could not detect desktop environment — focus detection may be limited." ;;
esac

# ALSA dev headers for python-rtmidi
if ! python3 -c "import rtmidi" &>/dev/null 2>&1; then
    ALSA_PKG=$(pkg_name alsa-dev)
    if ! pkg-config --exists alsa &>/dev/null 2>&1; then
        read -rp "  ALSA headers needed to build python-rtmidi. Install $ALSA_PKG? [Y/n] " response
        case "$response" in
            [nN]*) warn "Skipping — python-rtmidi may fail to install." ;;
            *)     pkg_install "$ALSA_PKG" ;;
        esac
    fi
fi

echo ""

# ── Clone / update repo ───────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.local/share/pipemixer-src"

info "Fetching PipeMixer source..."
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
    success "Source updated"
else
    git clone "$REPO" "$INSTALL_DIR"
    success "Source cloned"
fi

# Copy profile to user config so it survives pipx updates
mkdir -p "$HOME/.config/pipemixer/profiles"
cp "$INSTALL_DIR/profiles/nanokontrol2_mackie.json" "$HOME/.config/pipemixer/profiles/"
success "Profile installed to ~/.config/pipemixer/profiles/"

echo ""

# ── Install via pipx ──────────────────────────────────────────────────────────
info "Installing PipeMixer via pipx..."
pipx install --force "$INSTALL_DIR"
success "PipeMixer installed"

# Ensure ~/.local/bin is on PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    pipx ensurepath
    warn "Please restart your shell or run: source ~/.bashrc"
fi

echo ""

# ── Enable ydotool daemon ─────────────────────────────────────────────────────
if command -v ydotool &>/dev/null; then
    info "Enabling ydotool daemon..."
    systemctl --user enable --now ydotool 2>/dev/null \
        && success "ydotool daemon enabled" \
        || warn "Could not enable ydotool — arrow key simulation may not work"
fi

# ── Enable playerctld ─────────────────────────────────────────────────────────
if command -v playerctld &>/dev/null; then
    playerctld daemon &>/dev/null || true
fi

# ── Install systemd user service ──────────────────────────────────────────────
info "Installing systemd user service..."
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/pipemixer.service" << SERVICE
[Unit]
Description=PipeMixer — MIDI per-app volume mixer
After=pipewire.service wireplumber.service

[Service]
Type=simple
ExecStart=$HOME/.local/bin/pipemixer
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable pipemixer
systemctl --user start pipemixer
success "Service installed and started"

echo ""
echo -e "${GREEN}${BOLD}  PipeMixer installed successfully!${NC}"
echo ""
echo "  View logs:   journalctl --user -u pipemixer -f"
echo "  Restart:     systemctl --user restart pipemixer"
echo "  Stop:        systemctl --user stop pipemixer"
echo "  Uninstall:   pipx uninstall pipemixer && systemctl --user disable --now pipemixer"
echo ""
