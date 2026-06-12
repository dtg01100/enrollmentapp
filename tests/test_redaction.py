from pathlib import Path

from apple_device_cli.core.redaction import (
    _is_home_like_path,
    redact_address,
    redact_email,
    redact_identifier,
    redact_name,
    redact_org_identifier,
    redact_path,
    redact_phone,
    redact_url,
    sanitize_text,
)


def test_redact_url_hides_sensitive_path_segments():
    assert (
        redact_url("https://a.simplemdm.com/checkin/verysecrettokenvalue")
        == "https://a.simplemdm.com/checkin/…"
    )
    assert redact_url("https://mdm.example.com/mdm") == "https://mdm.example.com/mdm"


def test_redact_path_hides_home_directory_details():
    assert (
        redact_path("/var/home/example/.config/apple_device_cli/orgs/Test/cert.der")
        == "~/…/cert.der"
    )
    assert (
        redact_path("/home/example/.config/apple_device_cli/orgs/Test/cert.der") == "~/…/cert.der"
    )
    assert (
        redact_path("/Users/example/Downloads/example.mobileconfig") == "~/…/example.mobileconfig"
    )


def test_redact_identifier_preserves_only_edges():
    assert redact_identifier("d8b97d90b881aba50bd356599623578d32fb8da3") == "d8b97d…8da3"


def test_redact_org_identifier_handles_bundle_style_topics():
    assert (
        redact_org_identifier("com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f")
        == "com.apple.…"
    )


def test_redact_contact_fields():
    assert redact_email("owner@example.com") == "o…@…"
    assert redact_phone("5551234567") == "…4567"
    assert redact_address("123 Example Street, Exampletown, EX 12345") == "[redacted address]"


def test_redact_name_preserves_word_shape():
    assert redact_name("Example Device Company") == "E•••••• D••••• C••••••"


def test_sanitize_text_redacts_urls_paths_and_tokens():
    text = (
        "Check-in URL: https://mdm.example.com/checkin/abcdef1234567890abcdef1234567890abcdef12 "
        "File: /var/home/example/Downloads/example-wifi.mobileconfig "
        "Email: owner@example.com"
    )
    sanitized = sanitize_text(text)
    assert "https://mdm.example.com/checkin/…" in sanitized
    assert "~/…/example-wifi.mobileconfig" in sanitized
    assert "o…@…" in sanitized
    assert "abcdef" not in sanitized


def test_sanitize_text_redacts_home_paths_under_home():
    text = "WiFi config: /home/example/Downloads/example-wifi.mobileconfig"
    sanitized = sanitize_text(text)
    assert "~/…/example-wifi.mobileconfig" in sanitized


def test_sanitize_text_redacts_home_paths_under_users_and_custom_mounts():
    username = Path.home().name
    text = (
        "macOS path: /Users/example/Downloads/example-wifi.mobileconfig "
        f"Mounted path: /mnt/storage/{username}/.config/apple_device_cli/orgs/Test/cert.der"
    )
    sanitized = sanitize_text(text)
    assert "~/…/example-wifi.mobileconfig" in sanitized
    assert "~/…/cert.der" in sanitized


# --- Edge cases for full branch coverage ---


def test_is_home_like_path_recognises_supported_layouts():
    assert _is_home_like_path(Path("/home/u")) is True
    assert _is_home_like_path(Path("/var/home/u")) is True
    assert _is_home_like_path(Path("/Users/u")) is True
    assert _is_home_like_path(Path("/home/u/.config/orgs/Test")) is True
    assert _is_home_like_path(Path("/var/home/u/.config/orgs/Test")) is True
    assert _is_home_like_path(Path("/tmp/foo")) is False
    assert _is_home_like_path(Path("relative/path")) is False


def test_redact_name_handles_hyphens_and_single_chars():
    assert redact_name("Smith-Jones") == "S••••-J••••"
    assert redact_name("a-b-c") == "a-b-c"
    assert redact_name(None) == "Not set"
    assert redact_name("") == "Not set"


def test_redact_identifier_short_and_none():
    assert redact_identifier(None) == "Not set"
    assert redact_identifier("") == "Not set"
    # len <= prefix+suffix branch: keeps first char, masks rest
    assert redact_identifier("a") == "a"
    assert redact_identifier("abcdef") == "a•••••"
    # default 6+4 threshold
    assert redact_identifier("abcdefghijklmnop") == "abcdef…mnop"


def test_redact_org_identifier_branches():
    # 2-dot value: returned unchanged
    assert redact_org_identifier("com.example") == "com.example"
    # 1-dot bundle: keeps first 2 parts, dots after
    assert redact_org_identifier("com.apple.mgmt") == "com.apple.…"
    # 24+ hex: falls to redact_identifier with 4+4
    assert redact_org_identifier("0123456789abcdef01234567") == "0123…4567"
    # no dot, short string: redact_identifier
    assert redact_org_identifier("simple") == "s•••••"
    # None
    assert redact_org_identifier(None) == "Not set"


def test_redact_url_branches():
    assert redact_url(None) == "Not set"
    # No scheme -> returned unchanged
    assert redact_url("example.com/foo") == "example.com/foo"
    # Empty path (no segments)
    assert redact_url("https://example.com") == "https://example.com"
    # Trailing slash also means no segments -> empty path preserved
    assert redact_url("https://example.com/") == "https://example.com/"
    # Single short segment (<=12 chars) preserved
    assert redact_url("https://example.com/api") == "https://example.com/api"
    # Multi-segment path -> first segment + ellipsis
    assert redact_url("https://example.com/api/v1/secret") == "https://example.com/api/…"
    # Single long segment > 12 chars still gets second-segment ellipsis
    assert (
        redact_url("https://example.com/verylongsegmentname")
        == "https://example.com/verylongsegmentname/…"
    )
    # urlsplit raises ValueError -> original returned (defensive branch)
    from unittest.mock import patch

    with patch("apple_device_cli.core.redaction.urlsplit", side_effect=ValueError("bad url")):
        assert redact_url("https://example.com/secret") == "https://example.com/secret"


def test_redact_path_branches():
    # None
    assert redact_path(None) == "Not set"
    # Root has empty name -> returned as-is
    assert redact_path("/") == "/"
    # Single part (no directory) -> just the name
    assert redact_path("cert.der") == "cert.der"
    # Non-home multi-part path -> ellipsis + name
    assert redact_path("/tmp/foo/cert.der") == "…/cert.der"
    # Home-like path
    assert redact_path("/home/u/.config/x/cert.der") == "~/…/cert.der"
    # Accepts Path objects
    assert redact_path(Path("/tmp/foo/key.der")) == "…/key.der"


def test_redact_email_branches():
    assert redact_email(None) == "Not set"
    assert redact_email("") == "Not set"
    # Empty local part
    assert redact_email("@example.com") == "•…@…"
    # No '@' -> falls to redact_identifier
    assert redact_email("noatsign") == "n…noatsign"
    # Normal case
    assert redact_email("alice@example.com") == "a…@…"


def test_redact_phone_branches():
    assert redact_phone(None) == "Not set"
    # 4 digits or fewer -> just ellipsis
    assert redact_phone("1234") == "…"
    assert redact_phone("123") == "…"
    # 5+ digits -> last 4
    assert redact_phone("+1 (555) 123-4567") == "…4567"
    # Non-digit chars ignored; 4 digits in "abc12def34" -> still ellipsis
    assert redact_phone("abc12def34") == "…"


def test_redact_address_branches():
    assert redact_address(None) == "Not set"
    assert redact_address("") == "Not set"
    assert redact_address("123 Main St") == "[redacted address]"


def test_sanitize_text_branches():
    assert sanitize_text(None) == ""
    # Hex token of 24+ chars gets redacted (8+4 kept by redact_identifier)
    assert "a8b9c0…c2d3" in sanitize_text("token=a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3")
    # UUID 8-4-4-4-12: redact_identifier keeps 6+4 -> "123456…9012"
    assert "123456…9012" in sanitize_text("id=12345678-1234-1234-1234-123456789012")
    # Mixed: URL + email all in one string
    text = "URL=https://example.com/api/verysecrettoken email=alice@example.com"
    out = sanitize_text(text)
    assert "https://example.com/api/…" in out
    assert "a…@…" in out
    # /tmp/foo/cert.der under /tmp doesn't match the home-like regex
    # (no /home|/Users|/var/home) and doesn't include current user
    # after /, so it stays as-is — this is correct behavior.
    # The home-like patterns DO match when current username is in path:
    username = Path.home().name
    home_text = f"file=/var/home/{username}/.config/cert.der"
    assert "~/…/cert.der" in sanitize_text(home_text)
