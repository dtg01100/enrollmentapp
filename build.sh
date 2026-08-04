#!/usr/bin/env bash
# Build ios-enroll for the current platform using Nuitka.
#
# Usage:
#   ./build.sh                  # build everything (CLI + GUI) for the current platform
#   ./build.sh cli              # CLI only — installs no PySide6 dependency
#   ./build.sh gui              # GUI only — requires PySide6
#   ./build.sh windows          # Windows CLI + GUI via MinGW cross-compile
#   ./build.sh windows-cli      # Windows CLI only
#   ./build.sh windows-gui      # Windows GUI only
#   ./build.sh --clean [TARGET] # remove build artifacts before building
#
# The target is forwarded to build_nuitka.py. The right extras are
# installed per-target so a CLI-only build on a fresh checkout no longer
# pulls in PySide6 (and vice versa).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

TARGET="${1:-all}"

# ``--clean [TARGET]`` shifts the real target to $2. Detect that here so we
# install the right extras for whatever's about to be built.
if [[ "$TARGET" == "--clean" ]]; then
    TARGET="${2:-all}"
fi

# Targets that need the [gui] extra; everything else is plain install.
# All builds also need the [build] extra (Nuitka + helpers) since
# build_nuitka.py runs Nuitka regardless of the target.
case "$TARGET" in
    gui|all|windows|windows-gui)
        EXTRAS="build,gui"
        ;;
    cli|windows-cli)
        EXTRAS="build"
        ;;
    *)
        echo "build.sh: unknown target '$TARGET'" >&2
        echo "Valid targets: cli, gui, all, windows, windows-cli, windows-gui (or --clean [TARGET])" >&2
        exit 2
        ;;
esac

echo "=== Building ios-enroll (target: $TARGET) with Nuitka ==="

if [[ ! -d .venv-build ]]; then
    python3 -m venv .venv-build
fi

source .venv-build/bin/activate

pip install --upgrade pip
pip install -e ".[$EXTRAS]"

python3 build_nuitka.py "$@"

echo
echo "Build complete. Check dist/ for executables."
