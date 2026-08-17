"""Direct unit tests for the pure-decision functions in supervised_actions.

These tests cover the small state machines extracted from
``do_supervised_pairing`` — the orchestration code that decides what to do
when a cloud-config call raises, what state to record after a failed MDM
install attempt, and whether supervision was confirmed after a reconnect.

The functions being tested take only Python data (dicts, exceptions, ints,
bools) — no I/O, no pymobiledevice3 services — so they can be exercised
directly without the heavy mocking required by the async wrapper.

Pattern follows tests/test_supervised_helpers.py: small, focused, no
class-shaped mocks needed because the functions take primitives.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Functions/classes under test — import is lazy inside the failing-first
# verification so RED phase actually fails with ImportError.
from apple_device_cli.enrollment import supervised_actions as actions


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def desired_payload() -> dict:
    """A reasonable desired cloud-config payload for the matching tests."""
    return {
        "AllowPairing": True,
        "CloudConfigurationUIComplete": True,
        "ConfigurationSource": 2,
        "ConfigurationWasApplied": True,
        "IsMandatory": True,
        "IsMultiUser": False,
        "IsSupervised": True,
        "OrganizationName": "Acme",
        "PostSetupProfileWasInstalled": True,
    }


# ---------------------------------------------------------------------------
# Test: decide_already_present_outcome
# ---------------------------------------------------------------------------


class TestDecideAlreadyPresentOutcome:
    """The branch in do_supervised_pairing that handles
    CloudConfigurationAlreadyPresentError (lines 604-628 in supervised.py).
    """

    def test_matching_config_sets_config_and_no_errors(self, desired_payload):
        result = actions.decide_already_present_outcome(
            existing_config=desired_payload.copy(),
            desired_payload=desired_payload,
            check_error=None,
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: True,
        )
        assert result.config_set is True
        assert result.mdm_enrolled is False  # no mdm_url given
        assert result.error_messages == ()
        assert any("Matching" in m for m in result.progress_messages)

    def test_non_matching_config_records_error_and_unsets_config(self, desired_payload):
        existing = desired_payload.copy()
        existing["OrganizationName"] = "Other"
        result = actions.decide_already_present_outcome(
            existing_config=existing,
            desired_payload=desired_payload,
            check_error=None,
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: False,
        )
        assert result.config_set is False
        assert any("Cloud configuration mismatch" in m for m in result.error_messages)
        assert any("does NOT match" in m for m in result.progress_messages)

    def test_check_error_records_error_and_unsets_config(self, desired_payload):
        result = actions.decide_already_present_outcome(
            existing_config=None,
            desired_payload=desired_payload,
            check_error=OSError("connection died"),
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: True,
        )
        assert result.config_set is False
        assert any("could not be verified" in m for m in result.error_messages)
        assert any("Could not verify" in m for m in result.progress_messages)

    def test_matching_with_mdm_url_marks_mdm_enrolled(self, desired_payload):
        result = actions.decide_already_present_outcome(
            existing_config=desired_payload.copy(),
            desired_payload=desired_payload,
            check_error=None,
            mdm_url="https://mdm.example.com/mdm",
            mdm_checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.example.topic",
            matches_fn=lambda existing, desired: True,
        )
        assert result.config_set is True
        assert result.mdm_enrolled is True
        assert any("MDM enrollment URL" in m for m in result.progress_messages)
        assert any("Check-in URL" in m for m in result.progress_messages)
        assert any("MDM Topic" in m for m in result.progress_messages)

    def test_matching_without_mdm_url_does_not_mark_enrolled(self, desired_payload):
        result = actions.decide_already_present_outcome(
            existing_config=desired_payload.copy(),
            desired_payload=desired_payload,
            check_error=None,
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: True,
        )
        assert result.mdm_enrolled is False

    def test_progress_messages_for_matching_config_match_original_text(self, desired_payload):
        """The original code says
        'Matching cloud config already present - enrollment will proceed
        via Setup Assistant'. Pin the wording so a refactor that changes
        the user-visible message is caught."""
        result = actions.decide_already_present_outcome(
            existing_config=desired_payload.copy(),
            desired_payload=desired_payload,
            check_error=None,
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: True,
        )
        assert any(
            "Matching cloud config already present" in m
            for m in result.progress_messages
        )

    def test_mismatch_progress_message_matches_original_text(self, desired_payload):
        result = actions.decide_already_present_outcome(
            existing_config={"OrganizationName": "Other"},
            desired_payload=desired_payload,
            check_error=None,
            mdm_url=None,
            mdm_checkin_url=None,
            mdm_topic=None,
            matches_fn=lambda existing, desired: False,
        )
        assert any("does NOT match" in m for m in result.progress_messages)


# ---------------------------------------------------------------------------
# Test: decide_mdm_install_retry
# ---------------------------------------------------------------------------


class TestDecideMdmInstallRetry:
    """The retry decision in the MDM install loop (lines 765-774)."""

    def test_transient_first_attempt_returns_retry(self):
        exc = RuntimeError("network error: device offline")
        result = actions.decide_mdm_install_retry(
            attempt=1,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: True,
            format_fn=lambda prefix, err: f"{prefix}: {err}",
            fail_on_mdm_error=True,
        )
        assert result.action == "retry"
        assert "Retrying shortly" in result.error_message
        assert "1/3" in result.error_message

    def test_transient_last_attempt_records_error_not_retry(self):
        exc = RuntimeError("network error: device offline")
        result = actions.decide_mdm_install_retry(
            attempt=3,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: True,
            format_fn=lambda prefix, err: f"{prefix}: {err}",
            fail_on_mdm_error=True,
        )
        # attempt 3 == max_attempts: do NOT retry, record error
        assert result.action == "record_error"
        assert "Retrying shortly" not in result.error_message

    def test_non_transient_always_records_error_or_silent(self):
        exc = ValueError("denied")
        result = actions.decide_mdm_install_retry(
            attempt=1,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: False,
            format_fn=lambda prefix, err: f"{prefix}: {err}",
            fail_on_mdm_error=True,
        )
        assert result.action == "record_error"

    def test_non_transient_with_fail_off_returns_silent(self):
        exc = ValueError("denied")
        result = actions.decide_mdm_install_retry(
            attempt=1,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: False,
            format_fn=lambda prefix, err: f"{prefix}: {err}",
            fail_on_mdm_error=False,
        )
        # fail_on_mdm_error=False → don't append to errors, but still log
        assert result.action == "silent"

    def test_attempt_progress_message_includes_counter(self):
        exc = RuntimeError("network error: device offline")
        result = actions.decide_mdm_install_retry(
            attempt=2,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: True,
            format_fn=lambda prefix, err: f"{prefix}: {err}",
            fail_on_mdm_error=True,
        )
        assert "2/3" in result.error_message

    def test_format_fn_is_called_with_prefix_and_exception(self):
        seen: list[tuple[str, Exception]] = []
        exc = RuntimeError("boom")

        def fmt(prefix, err):
            seen.append((prefix, err))
            return f"{prefix}: {err}"

        actions.decide_mdm_install_retry(
            attempt=1,
            max_attempts=3,
            exception=exc,
            is_transient_fn=lambda e: False,
            format_fn=fmt,
            fail_on_mdm_error=True,
        )
        assert seen == [("MDM profile install failed", exc)]


# ---------------------------------------------------------------------------
# Test: classify_cloud_config_apply_error
# ---------------------------------------------------------------------------


class TestClassifyCloudConfigApplyError:
    """The error-classification branch (lines 604-634) — what's the next
    state when set_cloud_configuration raises?
    """

    def test_already_present_error_keeps_config_unset_no_disconnect(self):
        already = RuntimeError("already present")
        result = actions.classify_cloud_config_apply_error(
            exception=already,
            is_already_present_fn=lambda e: True,
        )
        assert result.config_set is False
        assert result.device_disconnected is False

    def test_broken_pipe_sets_config_and_disconnected(self):
        result = actions.classify_cloud_config_apply_error(
            exception=BrokenPipeError("pipe"),
            is_already_present_fn=lambda e: False,
        )
        assert result.config_set is True
        assert result.device_disconnected is True

    def test_connection_reset_sets_config_and_disconnected(self):
        result = actions.classify_cloud_config_apply_error(
            exception=ConnectionResetError("reset"),
            is_already_present_fn=lambda e: False,
        )
        assert result.config_set is True
        assert result.device_disconnected is True

    def test_oserror_sets_config_and_disconnected(self):
        result = actions.classify_cloud_config_apply_error(
            exception=OSError("network"),
            is_already_present_fn=lambda e: False,
        )
        assert result.config_set is True
        assert result.device_disconnected is True

    def test_other_exception_keeps_config_unset_no_disconnect(self):
        result = actions.classify_cloud_config_apply_error(
            exception=ValueError("denied"),
            is_already_present_fn=lambda e: False,
        )
        assert result.config_set is False
        assert result.device_disconnected is False


# ---------------------------------------------------------------------------
# Test: decide_post_reconnect_verification
# ---------------------------------------------------------------------------


class TestDecidePostReconnectVerification:
    """The verification step after reconnect (lines 643-651)."""

    def test_dict_with_supervised_true_returns_confirmed(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config={"IsSupervised": True, "OrganizationName": "Acme"},
            fetch_error=None,
        )
        assert result.supervised_confirmed is True
        assert "Supervision confirmed" in result.progress_message

    def test_dict_with_supervised_false_returns_not_confirmed(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config={"IsSupervised": False},
            fetch_error=None,
        )
        assert result.supervised_confirmed is False
        assert "not yet confirmed" in result.progress_message

    def test_none_dict_returns_not_confirmed(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config=None,
            fetch_error=None,
        )
        assert result.supervised_confirmed is False
        assert "not yet confirmed" in result.progress_message

    def test_non_dict_returns_not_confirmed(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config="unexpected",
            fetch_error=None,
        )
        assert result.supervised_confirmed is False

    def test_fetch_error_returns_not_confirmed_and_carries_error_text(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config=None,
            fetch_error=RuntimeError("reconnect blew up"),
        )
        assert result.supervised_confirmed is False
        assert "Could not verify supervision" in result.progress_message
        assert "reconnect blew up" in result.progress_message

    def test_dict_without_supervised_key_defaults_to_false(self):
        result = actions.decide_post_reconnect_verification(
            cloud_config={"OrganizationName": "Acme"},
            fetch_error=None,
        )
        assert result.supervised_confirmed is False
