VALID_PANES = {
    "location", "restore", "sim-setup", "android",
    "appleid", "intended-user", "siri", "screentime",
    "diagnostics", "software-update", "passcode", "touchid",
    "apple-pay", "zoom",
    "language", "region", "appearance",
    "language-and-locale", "express-language",
    "true-tone", "phone-number-permission", "home-button",
    "screen-saver", "tap-to-setup", "preferred-language-setup",
    "keyboard-setup", "dictation-setup", "watch-migration",
    "feature-highlights", "tv-provider", "tv-home-screen-sync",
    "privacy", "where-is-this-apple-tv", "imessage-and-facetime",
    "app-store", "safety", "multitasking", "action-button",
    "apple-intelligence", "camera-controls", "terms-of-address",
    "accessibility-appearance", "welcome",
    "restore-completed", "update-completed", "accessibility",
    "tos", "cloud-storage", "onboarding",
    "wifi", "display", "tone",
    "filevault", "icloud-storage", "icloud-diagnostics",
    "registration", "device-to-device-migration",
    "unlock-with-watch", "all",
    "avatar", "device-protection", "lockdown-mode",
    "wallpaper", "web-content-filtering",
    "age-based-safety", "tips",
}

PRESETS = {
    "minimal": [
        "language", "region", "appearance",
        "language-and-locale", "express-language",
        "restore-completed", "update-completed",
    ],
    "standard": [
        "restore-completed", "update-completed",
        "appleid", "passcode", "siri",
        "location", "home-button", "tos",
        "touchid", "apple-pay", "screentime",
        "diagnostics", "software-update",
        "privacy", "onboarding",
        "watch-migration", "registration",
        "cloud-storage", "device-to-device-migration",
    ],
    "all": list(VALID_PANES),
}


def resolve_skip_panes(preset: str | None, extra_panes: list[str] | None) -> list[str]:
    """Resolve skip panes from preset and extra pane list."""
    if extra_panes is None:
        extra_panes = []

    invalid = set(extra_panes) - VALID_PANES
    if invalid:
        raise ValueError(f"Invalid panes: {', '.join(sorted(invalid))}")

    result = set()
    if preset and preset in PRESETS:
        result.update(PRESETS[preset])
    result.update(extra_panes)

    return sorted(result)
