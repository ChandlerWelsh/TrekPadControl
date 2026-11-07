# TrekPad Control

Controls a NovaWalk W50 TrekPad walking pad (Merach, Bluetooth `MRK-T25-xxxx`) over standard
Bluetooth FTMS. Two apps share one protocol:

- **Windows** (`trekpad/`, Python): pywebview window, local HTTP API, hotkeys, exe via PyInstaller.
- **iOS** (`ios/`, SwiftUI): same controls, saves walks straight to Apple Health. TestFlight only.

Protocol, quirks and live-test results: `research/FINDINGS.md`. Read it before touching speed or
incline logic.

## Hard constraints

- **Safety logic must stay identical in both apps** (`trekpad/pad.py` and `ios/App/PadController.swift`):
  speed commands are in mph; after an incline change wait for the pad to confirm, then resend speed;
  settle any uncorrected jump before the next incline; hide only the single expected x1.6 jump;
  5 s of no data = dead link; max-speed cap; STOP after 3 failed corrections. Never test motor
  commands without the user standing by off the belt.
- **No Mac.** Nothing iOS builds locally. CI on GitHub's `xcode-27` runner is the only compiler
  (5–10 min a round). Run `python scripts/preflight.py` before every push (a hook enforces it).
- **Never commit `ios/TrekPad.xcodeproj`, `ios/Support/*-Info.plist` or `ios/Support/*.entitlements`.**
  XcodeGen generates them from `ios/project.yml`; edit that instead.
- iOS bundle ID `com.welshmandesign.trekpad` (team: Welshman Design, same account as Duo Stretch).
- Windows UI: never call pywebview window methods (resize, on_top, move) after start; they can
  deadlock. Use `trekpad/win32window.py`.
- Don't use Merach's or NovaWalk's branding as the app's own.

## Commands

- `python scripts/preflight.py`: Python tests + iOS static checks.
- `python -m unittest discover -s tests -t .`: Python tests only.
- `powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Install`: build and install the exe.
- CI: `.github/workflows/ci.yml` (preflight on Linux, then iOS simulator build + Swift tests).
  `.github/workflows/testflight.yml` (manual; cloud-managed signing, uploads to TestFlight;
  needs ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8, APPLE_TEAM_ID secrets).

## Workflow

One branch and PR per piece of work. After pushing, watch the PR's CI and fix failures.
Windows app changes: rebuild the exe and attach it to a GitHub release when asked.
