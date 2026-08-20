"""Test plugin: force rich's legacy_windows mode (simulates Windows CI).

On Windows CI, pytest captures stdout so rich's VT detection fails, and
rich substitutes ROUNDED panel boxes with SQUARE ones. This plugin forces
that mode so the suite can be validated on Linux.
"""

import rich.console as rich_console

_original_init = rich_console.Console.__init__


def _legacy_windows_init(self, *args, **kwargs):
    kwargs["legacy_windows"] = True
    _original_init(self, *args, **kwargs)


rich_console.Console.__init__ = _legacy_windows_init
