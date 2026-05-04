"""Helpers for redacting sensitive values in user-facing output."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ELLIPSIS = "…"
PERSONAL_PATH_MARKERS = {
    ".cache",
    ".config",
    ".local",
    ".ssh",
    "Desktop",
    "Documents",
    "Downloads",
    "Movies",
    "Music",
    "Pictures",
    "Public",
    "Templates",
    "Videos",
}


def _is_home_like_path(path: Path) -> bool:
    """Return True when a path appears to point inside a user's home directory."""
    home = Path.home()
    try:
        path.relative_to(home)
        return True
    except ValueError:
        pass

    parts = path.parts
    if len(parts) >= 4 and parts[:3] == ("/", "var", "home"):
        return True
    if len(parts) >= 3 and parts[:2] in {("/", "home"), ("/", "Users")}:
        return True

    username = home.name
    if username and username in parts:
        username_index = parts.index(username)
        trailing_parts = parts[username_index + 1 :]
        if any(part in PERSONAL_PATH_MARKERS or part.startswith(".") for part in trailing_parts):
            return True

    return False


def redact_name(value: str | None) -> str:
    """Redact a human-readable name while preserving rough shape."""
    if not value:
        return "Not set"

    def _redact_token(token: str) -> str:
        if len(token) <= 1:
            return token
        return token[0] + ("•" * (len(token) - 1))

    parts = re.split(r"(\s+|-)", value)
    return "".join(_redact_token(part) if part and not part.isspace() and part != "-" else part for part in parts)


def redact_identifier(value: str | None, prefix: int = 6, suffix: int = 4) -> str:
    """Redact an identifier while keeping short prefixes and suffixes visible."""
    if not value:
        return "Not set"
    if len(value) <= prefix + suffix:
        return value[0] + ("•" * max(0, len(value) - 1))
    return f"{value[:prefix]}{ELLIPSIS}{value[-suffix:]}"


def redact_org_identifier(value: str | None) -> str:
    """Redact organization IDs or MDM topics."""
    if not value:
        return "Not set"

    if re.fullmatch(r"[0-9a-fA-F-]{24,}", value):
        return redact_identifier(value, prefix=4, suffix=4)

    if "." in value:
        parts = value.split(".")
        if len(parts) <= 2:
            return value
        return ".".join(parts[:2]) + f".{ELLIPSIS}"

    return redact_identifier(value)


def redact_url(value: str | None) -> str:
    """Redact a URL path while preserving scheme and host."""
    if not value:
        return "Not set"

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    if not parsed.scheme or not parsed.netloc:
        return value

    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        redacted_path = parsed.path
    elif len(segments) == 1 and len(segments[0]) <= 12:
        redacted_path = f"/{segments[0]}"
    else:
        redacted_path = f"/{segments[0]}/{ELLIPSIS}"

    return urlunsplit((parsed.scheme, parsed.netloc, redacted_path, "", ""))


def redact_path(value: str | Path | None) -> str:
    """Redact a filesystem path while preserving the final filename."""
    if value is None:
        return "Not set"

    path = Path(value).expanduser()
    if path.name == "":
        return str(path)

    if _is_home_like_path(path):
        return f"~/{ELLIPSIS}/{path.name}"

    return path.name if len(path.parts) <= 1 else f"{ELLIPSIS}/{path.name}"


def redact_email(value: str | None) -> str:
    """Redact an email address."""
    if not value:
        return "Not set"
    local, _, domain = value.partition("@")
    if not domain:
        return redact_identifier(value, prefix=1, suffix=0)
    return f"{(local[:1] or '•')}{ELLIPSIS}@{ELLIPSIS}"


def redact_phone(value: str | None) -> str:
    """Redact a phone number leaving only the last four digits."""
    if not value:
        return "Not set"

    digits = [char for char in value if char.isdigit()]
    if len(digits) <= 4:
        return ELLIPSIS
    return f"{ELLIPSIS}{''.join(digits[-4:])}"


def redact_address(value: str | None) -> str:
    """Redact a physical address completely."""
    if not value:
        return "Not set"
    return "[redacted address]"


def sanitize_text(value: str | None) -> str:
    """Best-effort sanitization for arbitrary user-facing strings."""
    if value is None:
        return ""

    text = str(value)
    text = re.sub(
        r"https?://[^\s'\"]+",
        lambda match: redact_url(match.group(0)),
        text,
    )
    username = re.escape(Path.home().name)
    text = re.sub(
        r"(?:/var/home|/home|/Users)/[^/\s]+(?:/[^\s]*)?",
        lambda match: redact_path(match.group(0)),
        text,
    )
    text = re.sub(
        rf"/(?:[^/\s]+/)+{username}(?:/[^\s]*)?",
        lambda match: redact_path(match.group(0)),
        text,
    )
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        lambda match: redact_email(match.group(0)),
        text,
    )

    def _mask_long_token(match: re.Match[str]) -> str:
        token = match.group(0)
        return redact_identifier(token, prefix=6, suffix=4)

    text = re.sub(r"\b[0-9a-fA-F]{24,}\b", _mask_long_token, text)
    text = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", _mask_long_token, text)
    return text