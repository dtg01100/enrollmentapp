# Windows Installation Guide

This guide covers installing and running `ios-enroll` on Windows 10/11.

## Prerequisites

### 1. Install Python 3.10+

Download Python from [python.org](https://www.python.org/downloads/) or install via winget:

```powershell
winget install Python.Python.3.12
```

> Enable "Add Python to PATH" during installation.

### 2. Install Apple Mobile Device Support

`pymobiledevice3` requires Apple's **Mobile Device Service**, which is bundled with iTunes.

**Option A — Microsoft Store (recommended):**
```
Start -> Microsoft Store -> search "iTunes" -> Install
```

**Option B — Apple iTunes standalone:**
Download from [apple.com/itunes](https://www.apple.com/itunes/) and choose "iTunes for Windows (64-bit)" — *not* the Microsoft Store version if you want developer-driver access.

After installation:
1. Connect your iPhone or iPad via USB.
2. Open iTunes once so the drivers register.
3. Verify in **Device Manager -> Universal Serial Bus devices** that "Apple Mobile Device USB Composite Device" appears.

### 3. (Optional) Install libusb for Recovery/DFU workflows

Recovery and DFU mode require libusb. Download binaries from [libusb.info](https://libusb.info/) and place `libusb-1.0.dll` next to the `ios-enroll.exe` (or in `C:\Windows\System32`).

Only needed if you plan to use `pymobiledevice3 restore`.

---

## Installation

### Option A — Install from PyPI (recommended for command-line use)

```powershell
pip install ios-enroll[gui]
```

Launch the CLI:
```powershell
ios-enroll --help
```

Launch the GUI:
```powershell
ios-enroll --gui
```

A second shortcut is also installed:
```powershell
ios-enroll-gui
```

### Option B — Standalone executables (no Python required)

Download the latest `ios-enroll-dist.zip` from the project's release page, extract to a folder of your choice, and run:

```powershell
# CLI
dist\ios-enroll.exe --help
dist\ios-enroll.exe --gui

# GUI (double-click works too)
dist\ios-enroll-gui.exe
```

The bundled executables include Python, all dependencies, and the GUI runtime. No Python install is required on the target machine.

---

## Trusting the device

The first time you connect an iPhone:

1. Tap **Trust** on the iPhone's "Trust This Computer?" prompt.
2. Pair the device from the CLI:
   ```powershell
   ios-enroll device list
   ```
3. If `device list` returns no devices, run the **Pair/Trust** button in the GUI, or:
   ```powershell
   pymobiledevice3 lockdown pair
   ```

---

## Troubleshooting

### "No devices found"

- Replug the USB cable and ensure it's a data cable (not charge-only).
- Open iTunes and confirm the device shows up there.
- Check Device Manager — "Apple Mobile Device USB Composite Device" should appear under USB devices.
- Reinstall [Apple Mobile Device Support](https://support.apple.com/en-us/HT204095).

### `usbmuxd` connection errors

`pymobiledevice3` connects to Apple's Mobile Device Service over a local TCP socket (port `27015`) on Windows. If it fails:

```powershell
netstat -ano | findstr :27015
```

You should see `AppleMobileDeviceProcess.exe` listening. Restart iTunes or run:

```powershell
net stop "Apple Mobile Device Service"
net start "Apple Mobile Device Service"
```

### `win32api` import errors

The `pyusb`-backed recovery flow needs `pywin32`. Install it manually:

```powershell
pip install pywin32
```

### Antivirus false positives

Nuitka-compiled onefile bundles are sometimes flagged by antivirus software because they embed a C-compiled Python runtime. This is a known false positive — submit the bundle to your AV vendor as a false positive, or distribute via internal signing.

### iOS 17+ tunnel errors

iOS 17 introduced tunnel-based transport. On Windows:

- For iOS 17.0–17.3.1 over QUIC/Wi-Fi: run a privileged shell and use `pymobiledevice3 remote tunneld`.
- For iOS 17.4+ USB: no extra step — `pymobiledevice3` uses the lockdown tunnel automatically.
- For developer commands without admin: pass `--userspace` to use the in-process PyTCP tunnel (Python 3.9+).

See [pymobiledevice3 iOS 17 tunnels guide](https://github.com/doronz88/pymobiledevice3/blob/master/docs/guides/ios17-tunnels.md).

---

## Building from source on Windows

If you want to produce your own Windows executables:

```cmd
git clone https://github.com/example/ios-enroll
cd ios-enroll
build_windows.bat
```

Outputs go to `dist\ios-enroll.exe` (CLI) and `dist\ios-enroll-gui.exe` (GUI).

For Linux/macOS development builds:

```bash
./build.sh
```

> Cross-compilation note: PyInstaller is **not** a cross-compiler. To produce Windows `.exe` files you must run `build_windows.bat` on a Windows machine (or in a Windows CI runner such as `windows-latest` GitHub Actions).

---

## Differences from macOS / Linux behavior

- `usbmuxd` is replaced by Apple's Mobile Device Service — no separate daemon to start.
- `fcntl` file locks in `OrganizationManager` fall back to Windows `msvcrt` locks automatically (no code change required; uses `fcntl`/`os.open` cross-platform wrappers already in stdlib).
- The default org storage path is `%APPDATA%\apple_device_cli\orgs\` instead of `~/.config/apple_device_cli/orgs/`.
- The GUI uses **PySide6** instead of tkinter because tkinter + X11 forwarding is fragile on Windows; PySide6 uses the native Win32 / Direct2D stack.

---

## Uninstall

```powershell
pip uninstall ios-enroll
```

For standalone bundles, just delete the extracted folder.
