"""Package entrypoint: ``python -m apple_device_cli`` or Nuitka compile target.

Allows Nuitka to compile this whole package as a single entry while
preserving the package's import context (so ``from apple_device_cli
import __version__`` works inside cli.py).
"""
from apple_device_cli.cli import main

if __name__ == "__main__":
    main()
