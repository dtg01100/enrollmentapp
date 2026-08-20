"""Tests for the new MDM-inspect CLI commands.

Covers the no-device paths (the part that doesn't require a real iOS
device): error output structure, JSON contract, and the destructive
``profile remove`` confirmation gate.

The actual service calls are exercised by ``test_mdm_inspect.py``; here
we only verify the CLI plumbing.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from apple_device_cli.cli import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# device list-apps / network / certs / security-info
# ---------------------------------------------------------------------------


def test_device_list_apps_requires_udid():
    """Without --udid, the command must refuse (no interactive picker)."""
    result = runner.invoke(app, ["device", "list-apps"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_device_network_requires_udid():
    result = runner.invoke(app, ["device", "network"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_device_certs_requires_udid():
    result = runner.invoke(app, ["device", "certs"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_device_security_info_requires_udid():
    result = runner.invoke(app, ["device", "security-info"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_device_list_apps_json_error_on_service_failure():
    """When the service raises and --json is set, output must be parseable JSON."""
    with patch("apple_device_cli.cli.asyncio.run", side_effect=RuntimeError("boom")):
        result = runner.invoke(
            app, ["device", "list-apps", "--udid", "FAKE-UDID", "--json"]
        )
    assert result.exit_code == 1
    parsed = json.loads(result.output.strip())
    assert "error" in parsed
    assert "boom" in parsed["error"]


def test_device_network_json_error_on_service_failure():
    with patch("apple_device_cli.cli.asyncio.run", side_effect=RuntimeError("net down")):
        result = runner.invoke(
            app, ["device", "network", "--udid", "FAKE-UDID", "--json"]
        )
    assert result.exit_code == 1
    parsed = json.loads(result.output.strip())
    assert "net down" in parsed["error"]


def test_device_certs_json_error_on_service_failure():
    with patch("apple_device_cli.cli.asyncio.run", side_effect=RuntimeError("misagent down")):
        result = runner.invoke(
            app, ["device", "certs", "--udid", "FAKE-UDID", "--json"]
        )
    assert result.exit_code == 1
    parsed = json.loads(result.output.strip())
    assert "misagent down" in parsed["error"]


def test_device_security_info_json_error_on_service_failure():
    with patch("apple_device_cli.cli.asyncio.run", side_effect=RuntimeError("diag down")):
        result = runner.invoke(
            app, ["device", "security-info", "--udid", "FAKE-UDID", "--json"]
        )
    assert result.exit_code == 1
    parsed = json.loads(result.output.strip())
    assert "diag down" in parsed["error"]


# ---------------------------------------------------------------------------
# profile list / remove
# ---------------------------------------------------------------------------


def test_profile_list_requires_udid():
    result = runner.invoke(app, ["profile", "list"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_profile_remove_requires_udid():
    result = runner.invoke(app, ["profile", "remove", "com.example.profile"])
    assert result.exit_code == 1
    assert "--udid is required" in result.output


def test_profile_remove_non_interactive_without_yes():
    """Piped stdin (non-TTY) must require --yes to remove a profile."""
    # CliRunner's default is non-TTY-like behavior; ensure we cannot remove
    # without --yes.  The action itself is also blocked by ensure_device_pairing
    # which is fine — what we're asserting is that the gate fires first.
    result = runner.invoke(
        app, ["profile", "remove", "--udid", "FAKE", "com.example.profile"]
    )
    # Exit 1 from the confirmation gate (refuses non-interactive without --yes)
    assert result.exit_code == 1
    assert "Refusing to remove" in result.output or "--udid is required" in result.output


def test_profile_remove_json_error_on_service_failure():
    """--yes + --udid but the service raises: the error path is exercised."""
    with patch("apple_device_cli.cli.asyncio.run", side_effect=RuntimeError("svc down")):
        result = runner.invoke(
            app,
            [
                "profile", "remove", "--udid", "FAKE", "--yes",
                "com.example.profile",
            ],
        )
    assert result.exit_code == 1
    assert "svc down" in result.output or "Error" in result.output
