# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in ios-enroll, please report it responsibly:

- **Do not** file a public GitHub issue for security vulnerabilities.
- Open a private [GitHub Security Advisory](../../security/advisories/new) or contact the maintainers directly.

## Sensitive Data Handling

ios-enroll handles sensitive cryptographic materials:

- **Supervising certificates** (`.der`) and **private keys** (`.der`) are stored locally in `~/.config/apple_device_cli/orgs/`
- **PKCS12 identities** (`.organization`, `.p12`) are imported with a default password (`password`) as used by Apple Configurator
- **MDM profiles** (`.mobileconfig`) may contain Wi-Fi passwords and server URLs

These files are listed in `.gitignore` and should **never** be committed to version control.

## Credential Storage

Organization credentials are stored on-disk in the user's home directory. Access to these files should be restricted using filesystem permissions (`chmod 600`).

If you believe credentials have been accidentally committed, rotate them immediately and open a security report.
