#!/usr/bin/env bash
set -euo pipefail

echo "=== Datamosh deps installer (macOS) ==="

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is intended for macOS. Aborting."
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "python3 not found. Install Xcode Command Line Tools or Python, then re-run."
  echo "Hint: xcode-select --install"
  exit 1
fi

# --- Homebrew & FFmpeg -------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Installing Homebrew..."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -d "/opt/homebrew/bin" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -d "/usr/local/bin" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

# Ensure brew on PATH
if ! command -v brew >/dev/null 2>&1; then
  if [[ -x "/opt/homebrew/bin/brew" ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x "/usr/local/bin/brew" ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    echo "Homebrew not on PATH. Aborting." >&2
    exit 1
  fi
fi

# Install ffmpeg if missing
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Installing FFmpeg via Homebrew..."
  brew update
  brew install ffmpeg
else
  echo "FFmpeg already installed: $(ffmpeg -version | head -n1)"
fi

# --- Python virtualenv -------------------------------------------------------
VENV_DIR="mosh-venv"
if [[ -d "$VENV_DIR" ]]; then
  echo "Using existing venv: $VENV_DIR"
else
  echo "Creating venv: $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel

# --- Python packages ---------------------------------------------------------
echo "Installing Python packages: numpy, opencv-python-headless, av, tqdm"
pip install numpy opencv-python-headless av tqdm

echo "Verifying installs..."
python - <<'PY'
import sys, subprocess
print("Python:", sys.version.split()[0])
try:
    import cv2
    print("OpenCV:", cv2.__version__)
except Exception as e:
    print("OpenCV import failed:", e)

try:
    import av
    print("PyAV:", av.__version__)
except Exception as e:
    print("PyAV import failed:", e)

try:
    out = subprocess.check_output(["ffmpeg","-version"]).decode().splitlines()[0]
    print(out)
except Exception as e:
    print("FFmpeg check failed:", e)
PY

# --- project convenience: videosrc folder for scanning ---
mkdir -p videosrc
echo "Created ./videosrc (drop input clips here to use --scan or run without -f)"

echo
echo "✅ Done. Activate with:"
echo "   source $VENV_DIR/bin/activate"
echo
echo "Run e.g.: python main.py -f input.mp4 -a flow_leaky -o out.mp4 -v"
