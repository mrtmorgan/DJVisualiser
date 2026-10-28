#!/usr/bin/env bash
# One-time setup on macOS (Apple Silicon). Installs ffmpeg and Python via Homebrew,
# creates a virtual environment in the repo, and runs the test suite.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed. Install it from https://brew.sh, then run this script again." >&2
  exit 1
fi

brew install ffmpeg python git

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m unittest discover -s tests -t .

echo
echo "Setup complete. Activate the environment in a new Terminal window with:"
echo "  cd $(pwd) && source .venv/bin/activate"
