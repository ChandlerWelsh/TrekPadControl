# TrekPad for iPhone

SwiftUI version of TrekPad Control: the same click-to-set speed gauge and incline bars, the same
safety logic, and walks saved straight to **Apple Health** as indoor walking workouts (with
per-minute distance and active energy). Built the same way as Duo Stretch: no Mac, GitHub Actions
compiles it, and TestFlight installs it.

## How it's built

- `project.yml`: XcodeGen spec. CI generates the Xcode project; it's never committed.
- `App/`: `FTMS.swift` (protocol), `PadController.swift` (Bluetooth + safety, a port of
  `trekpad/pad.py`), `WalkRecorder.swift`, `HealthSync.swift`, `ContentView.swift` (UI).
- `Tests/`: logic tests run on a simulator in CI, using the same pad packets as the Python tests.
- Every push to a PR runs `.github/workflows/ci.yml` (simulator build and tests, unsigned).
- `.github/workflows/testflight.yml` (run by hand) signs with cloud-managed signing and uploads
  to TestFlight.

## One-time setup (browser only)

Your Apple Developer account and the App Store Connect API key from Duo Stretch already work for
this. The key is team-wide, so there's nothing new to create.

1. **Register the bundle ID:** [developer.apple.com](https://developer.apple.com/account/resources/identifiers/list)
   → Certificates, IDs & Profiles → Identifiers → **+** → App IDs → App. Description "TrekPad",
   Bundle ID (explicit) `com.welshmandesign.trekpad`, and tick **HealthKit** under Capabilities.
2. **Create the app record:** App Store Connect → Apps → **+ → New App**. Platform iOS, any
   unique name (for example "TrekPad Walk Control"), bundle ID `com.welshmandesign.trekpad`,
   any SKU (for example `trekpad`), Full Access. It stays private: TestFlight internal testing
   needs no review and no store listing.
3. **Add the four secrets to this repo:** GitHub → TrekPadControl → Settings → Secrets and
   variables → Actions → New repository secret. The same values as DuoMax:
   `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (the `.p8` file's text) and `APPLE_TEAM_ID`.
   GitHub can't copy secrets between repos, so you need the `.p8` file you saved for Duo Stretch.
   If it's gone, make a new **Admin** key in App Store Connect → Users and Access → Integrations
   and use that.
4. **Build:** GitHub → Actions → **TestFlight (iOS)** → Run workflow. About 10 minutes, then 5–30
   minutes of Apple processing.
5. **Install:** open TestFlight on your iPhone; TrekPad appears there (you're automatically an
   internal tester).

## First run

- Close the Merach app and the Windows TrekPad app: the pad accepts one connection at a time.
- Allow Bluetooth. Apple Health permission is requested the first time a walk is saved.
- Walks are kept on the phone until they're saved, so nothing is lost if Health access is off.
- If you also start a walk on your Apple Watch, Health will show two workouts. Pick one.
