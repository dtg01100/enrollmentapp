"""Docs-drift test: the flag tables in ``docs/cli/`` must match the real CLI.

For every command documented in ``docs/cli/{device,org,enroll}.md``, this
parses the documented flags out of the markdown tables and compares them
with the flags the Typer app actually registers (via ``--help``). It catches:

- a new CLI flag that was never documented,
- a documented flag that was renamed or removed,
- doc typos (the old ``--wifi-config`` vs the real ``--path`` for
  ``org set-wifi``).

Commands without flags (e.g. ``enroll guided-enroll``) are documented with
a one-line note and no table — the expected set is then empty.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apple_device_cli.cli import app

# Rich truncates long flag names (e.g. --no-fail-on-mdm-error) in the
# default 80-column help box; widen the terminal so --help shows full names.
# Assignment (not setdefault): a shell/CI-inherited narrow COLUMNS would
# otherwise win and truncate flags again.
os.environ["COLUMNS"] = "200"

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "cli"
# doc file -> CLI group name (the docs name their sections `group cmd`).
GROUP_BY_FILE = {"device.md": "device", "org.md": "org", "enroll.md": "enroll"}

runner = CliRunner()

_LONG_FLAG = re.compile(r"--[a-z0-9][a-z0-9-]*")
# Single-char short flag (-y, -v, -f, -C, -K, -p) — never part of a --flag.
_SHORT_FLAG = re.compile(r"(?<![\w-])-[A-Za-z0-9](?![-\w])")
# Section headings in the docs are exactly: ## `group command`
_HEADING = re.compile(r"^## `([a-z-]+) ([a-z0-9-]+)`$", re.M)


def _help_flags(group: str, command: str) -> set[str]:
    """The flags the CLI actually registers for ``group command``."""
    result = runner.invoke(app, [group, command, "--help"])
    assert result.exit_code == 0, f"`{group} {command} --help` failed:\n{result.output}"
    # GitHub Actions sets GITHUB_ACTIONS, which makes typer force terminal
    # mode (FORCE_TERMINAL) and emit ANSI escape codes around every styled
    # segment. Strip them so the literal box characters are contiguous and
    # the panel regex below still matches.
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    box = re.search(r"╭─ Options ─.*?╰─", output, re.S)
    assert box is not None, f"no Options box in `{group} {command} --help`"
    flags = set(_LONG_FLAG.findall(box.group(0))) | set(_SHORT_FLAG.findall(box.group(0)))
    flags.discard("--help")  # implicit click option, never documented
    return flags


def _documented_flags(md: str, command: str) -> set[str]:
    """The flags documented in the markdown table(s) for one command section."""
    m = re.search(r"^## `[a-z-]+ " + re.escape(command) + r"`$", md, re.M)
    assert m is not None, f"no docs section found for `{command}`"
    end = md.find("\n## ", m.end())
    section = md[m.end():] if end == -1 else md[m.end():end]
    flags: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue  # only flag-table rows count; prose notes are not parsed
        for token in re.findall(r"`([^`]+)`", line):
            flags |= set(_LONG_FLAG.findall(token))
            flags |= set(_SHORT_FLAG.findall(token))
    return flags


def _collect() -> list[tuple[str, str, Path]]:
    cases: list[tuple[str, str, Path]] = []
    for filename, group in GROUP_BY_FILE.items():
        path = DOCS_DIR / filename
        md = path.read_text()
        for heading_group, command in _HEADING.findall(md):
            assert heading_group == group, (
                f"{filename}: section `{heading_group} {command}` does not "
                f"match the file's group `{group}`"
            )
            cases.append((group, command, path))
    assert cases, "no commands found in docs/cli/"
    return cases


_COMMANDS = _collect()


@pytest.mark.parametrize(
    "group,command,path",
    [pytest.param(g, c, p, id=f"{g} {c}") for g, c, p in _COMMANDS],
)
def test_documented_flags_match_cli(group: str, command: str, path: Path) -> None:
    md = path.read_text()
    documented = _documented_flags(md, command)
    actual = _help_flags(group, command)
    missing_in_docs = actual - documented
    stale_in_docs = documented - actual
    assert not missing_in_docs and not stale_in_docs, (
        f"`{group} {command}` flag drift.\n"
        f"  In the CLI but not documented: {sorted(missing_in_docs)}\n"
        f"  Documented but not in the CLI: {sorted(stale_in_docs)}\n"
        f"  Update docs/cli/{path.name}."
    )
