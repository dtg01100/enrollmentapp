"""Pure-decision functions extracted from supervised.do_supervised_pairing.

The ``do_supervised_pairing`` flow mixes pymobiledevice3 I/O with branching
decisions about what state to record and what message to log. This module
extracts the **pure** parts — the ones that take only Python data and
return a small dataclass describing what the caller should do.

The async wrapper in ``supervised.py`` calls these functions to decide what
flags to set and what messages to log, then performs the I/O the decision
tells it to.

Rules for this module:
- No I/O. No ``asyncio``. No ``pymobiledevice3`` imports. The functions here
  must be importable in a test environment that has nothing but stdlib.
- Functions are typed dataclasses or primitives. No callbacks into async
  machinery.
- Side-effect-y helpers (formatting, comparison) are injected so the pure
  function can be unit-tested without pulling in redaction / sanitize_text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlreadyPresentDecision:
    """Outcome of handling ``CloudConfigurationAlreadyPresentError``.

    Attributes:
        config_set: Whether the existing config matches the desired payload
            (and we can proceed). ``False`` when the config mismatches OR the
            verification step itself failed.
        mdm_enrolled: Whether to record MDM enrollment based on the existing
            config (set when ``config_set`` is true AND ``mdm_url`` was
            provided).
        progress_messages: Ordered human-readable lines for the caller to
            log via ``_progress()``.
        error_messages: Ordered error lines for the caller to append to the
            ``errors`` list on the enrollment result.
    """

    config_set: bool
    mdm_enrolled: bool
    progress_messages: tuple[str, ...] = field(default_factory=tuple)
    error_messages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class MdmRetryDecision:
    """Outcome of a single failed MDM-profile install attempt.

    Attributes:
        action: One of ``"retry"`` (loop continues with backoff),
            ``"record_error"`` (loop exits AND error appended), or
            ``"silent"`` (loop exits, error NOT appended, message still
            logged for the user).
        error_message: The user-visible message the caller should log.
            For ``"retry"``, it includes the attempt counter.
    """

    action: str
    error_message: str


@dataclass(frozen=True)
class SupervisionState:
    """State flags to record after a ``set_cloud_configuration`` call.

    Attributes:
        config_set: ``True`` if the call succeeded OR the device disconnected
            after a successful apply (the config landed). ``False`` for
            AlreadyPresent errors that need separate handling, or other
            failures.
        device_disconnected: ``True`` if the call ended in a broken-pipe /
            connection-reset / OSError — meaning the caller must attempt a
            reconnect and re-verify.
    """

    config_set: bool
    device_disconnected: bool


@dataclass(frozen=True)
class VerificationResult:
    """Result of the post-reconnect supervision check.

    Attributes:
        supervised_confirmed: Whether the device reports ``IsSupervised``
            after the reconnect.
        progress_message: The line to log describing the verification
            outcome (success, not-yet-confirmed, or fetch error).
    """

    supervised_confirmed: bool
    progress_message: str


# ---------------------------------------------------------------------------
# decide_already_present_outcome
# ---------------------------------------------------------------------------


def decide_already_present_outcome(
    existing_config: dict[str, Any] | None,
    desired_payload: dict[str, Any],
    check_error: Exception | None,
    mdm_url: str | None,
    mdm_checkin_url: str | None,
    mdm_topic: str | None,
    matches_fn: Callable[[dict[str, Any], dict[str, Any]], bool],
    format_url_fn: Callable[[str | None], str] | None = None,
    format_topic_fn: Callable[[str | None], str] | None = None,
) -> AlreadyPresentDecision:
    """Decide what state to record after ``CloudConfigurationAlreadyPresentError``.

    Mirrors the logic in ``do_supervised_pairing`` lines 604-628: when the
    device already has a cloud config, we read it back and decide whether
    it matches what we want. If it matches, we proceed (and record MDM
    enrollment if an MDM URL was provided). If it doesn't match, we record
    an error. If we couldn't read it back, we record a different error.

    Args:
        existing_config: The dict returned by ``get_cloud_configuration()``,
            or ``None`` if the read failed.
        desired_payload: The cloud-config dict we tried to set.
        check_error: ``None`` if the read succeeded; otherwise the exception
            raised by the read attempt.
        mdm_url: Optional MDM enrollment URL the caller passed in.
        mdm_checkin_url: Optional MDM check-in URL.
        mdm_topic: Optional MDM topic identifier.
        matches_fn: Callable that returns ``True`` when ``existing_config``
            is equivalent to ``desired_payload``. Injected so the pure
            function doesn't import ``_cloud_config_matches`` (which keeps
            this module testable without supervised.py machinery).
        format_url_fn: Optional callable that redacts a URL for display.
            Defaults to ``str``. The wrapper passes ``redact_url`` so
            sensitive path segments aren't logged in plaintext.
        format_topic_fn: Optional callable that redacts a topic identifier
            for display. Defaults to ``str``. The wrapper passes
            ``redact_org_identifier``.

    Returns:
        :class:`AlreadyPresentDecision` — flags the caller sets and messages
        it logs/appends.
    """
    fmt_url = format_url_fn or (lambda v: str(v) if v is not None else "None")
    fmt_topic = format_topic_fn or (lambda v: str(v) if v is not None else "None")

    progress: list[str] = []
    errors: list[str] = []
    config_set = False

    if check_error is not None:
        progress.append(f"Could not verify existing cloud config: {check_error}")
        errors.append(
            f"Cloud configuration already present and could not be verified: {check_error}"
        )
    elif existing_config and matches_fn(existing_config, desired_payload):
        progress.append(
            "Matching cloud config already present - enrollment will proceed via Setup Assistant"
        )
        config_set = True
    else:
        progress.append("Cloud config already present but does NOT match desired configuration.")
        errors.append(
            "Cloud configuration mismatch: device already has different supervision settings"
        )

    mdm_enrolled = False
    if config_set and mdm_url:
        progress.append(f"MDM enrollment URL in existing cloud config: {fmt_url(mdm_url)}")
        if mdm_checkin_url:
            progress.append(f"Check-in URL: {fmt_url(mdm_checkin_url)}")
        if mdm_topic:
            progress.append(f"MDM Topic: {fmt_topic(mdm_topic)}")
        mdm_enrolled = True

    return AlreadyPresentDecision(
        config_set=config_set,
        mdm_enrolled=mdm_enrolled,
        progress_messages=tuple(progress),
        error_messages=tuple(errors),
    )


# ---------------------------------------------------------------------------
# decide_mdm_install_retry
# ---------------------------------------------------------------------------


def decide_mdm_install_retry(
    attempt: int,
    max_attempts: int,
    exception: Exception,
    is_transient_fn: Callable[[Exception], bool],
    format_fn: Callable[[str, Exception], str],
    fail_on_mdm_error: bool,
) -> MdmRetryDecision:
    """Decide whether the MDM install loop should retry, give up loudly, or quit silently.

    Mirrors the logic in ``do_supervised_pairing`` lines 765-774.

    Args:
        attempt: 1-indexed attempt number that just failed.
        max_attempts: Total attempts allowed (loop bound).
        exception: The exception raised by the install attempt.
        is_transient_fn: Callable that returns ``True`` if the exception
            describes a transient network condition safe to retry on.
        format_fn: Callable ``(prefix, exception) -> str`` that produces
            the user-visible error message. Injected so the pure function
            doesn't import ``_format_mobileconfig_error``.
        fail_on_mdm_error: If ``True``, an unrecoverable failure appends
            to the enrollment ``errors`` list. If ``False``, the failure
            is logged but not recorded as a hard error.

    Returns:
        :class:`MdmRetryDecision` — the action and the message to log.
    """
    error_msg = format_fn("MDM profile install failed", exception)

    if attempt < max_attempts and is_transient_fn(exception):
        return MdmRetryDecision(
            action="retry",
            error_message=f"{error_msg} Retrying shortly ({attempt}/{max_attempts})...",
        )

    if fail_on_mdm_error:
        return MdmRetryDecision(action="record_error", error_message=error_msg)

    return MdmRetryDecision(action="silent", error_message=error_msg)


# ---------------------------------------------------------------------------
# classify_cloud_config_apply_error
# ---------------------------------------------------------------------------


def classify_cloud_config_apply_error(
    exception: Exception,
    is_already_present_fn: Callable[[Exception], bool],
) -> SupervisionState:
    """Classify a failed ``set_cloud_configuration`` call into state flags.

    Mirrors the branching in ``do_supervised_pairing`` lines 604-634. Three
    outcomes are possible:

    - ``CloudConfigurationAlreadyPresentError``: caller must read back the
      existing config and verify (handled separately).
    - ``BrokenPipeError`` / ``ConnectionResetError`` / ``OSError``: the
      device disconnected after the apply started — caller must reconnect.
    - Anything else: caller should record a hard error.

    Args:
        exception: The exception raised by ``set_cloud_configuration``.
        is_already_present_fn: Callable that returns ``True`` if
            ``exception`` is a ``CloudConfigurationAlreadyPresentError``.
            Injected so this module doesn't import pymobiledevice3.

    Returns:
        :class:`SupervisionState` describing what flags the caller should
        set on the enrollment result.
    """
    if is_already_present_fn(exception):
        return SupervisionState(config_set=False, device_disconnected=False)

    if isinstance(exception, (BrokenPipeError, ConnectionResetError, OSError)):
        return SupervisionState(config_set=True, device_disconnected=True)

    return SupervisionState(config_set=False, device_disconnected=False)


# ---------------------------------------------------------------------------
# decide_post_reconnect_verification
# ---------------------------------------------------------------------------


def decide_post_reconnect_verification(
    cloud_config: dict[str, Any] | None,
    fetch_error: Exception | None,
) -> VerificationResult:
    """Decide whether supervision is confirmed after a device reconnect.

    Mirrors the verification step in ``do_supervised_pairing`` lines 643-651
    (and 798-811). If the reconnect succeeded and the device reports
    ``IsSupervised``, supervision is confirmed. Otherwise it isn't, and a
    human-readable line is returned for the caller to log.

    Args:
        cloud_config: The dict returned by ``get_cloud_configuration()`` on
            the fresh lockdown, or ``None`` if the read failed.
        fetch_error: The exception raised by the read attempt, or ``None``
            on success.

    Returns:
        :class:`VerificationResult` — confirmation bool and the message
        the caller should log.
    """
    if fetch_error is not None:
        return VerificationResult(
            supervised_confirmed=False,
            progress_message=f"Could not verify supervision after reconnect: {fetch_error}",
        )

    if isinstance(cloud_config, dict) and cloud_config.get("IsSupervised"):
        return VerificationResult(
            supervised_confirmed=True,
            progress_message=f"Supervision confirmed: {cloud_config.get('OrganizationName')}",
        )

    return VerificationResult(
        supervised_confirmed=False,
        progress_message="Supervision not yet confirmed, continuing with WiFi and MDM install",
    )
