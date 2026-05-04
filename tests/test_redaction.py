from pathlib import Path

from apple_device_cli.core.redaction import (
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
    assert redact_url("https://a.simplemdm.com/checkin/verysecrettokenvalue") == "https://a.simplemdm.com/checkin/…"
    assert redact_url("https://mdm.example.com/mdm") == "https://mdm.example.com/mdm"


def test_redact_path_hides_home_directory_details():
    assert redact_path("/var/home/example/.config/apple_device_cli/orgs/Test/cert.der") == "~/…/cert.der"
    assert redact_path("/home/example/.config/apple_device_cli/orgs/Test/cert.der") == "~/…/cert.der"
    assert redact_path("/Users/example/Downloads/example.mobileconfig") == "~/…/example.mobileconfig"


def test_redact_identifier_preserves_only_edges():
    assert redact_identifier("d8b97d90b881aba50bd356599623578d32fb8da3") == "d8b97d…8da3"


def test_redact_org_identifier_handles_bundle_style_topics():
    assert redact_org_identifier("com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f") == "com.apple.…"


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