"""Everything that can be checked without a Mac. Run before every push (a Claude Code hook
enforces it): python scripts/preflight.py

- Windows app: Python unit tests
- iOS: project.yml paths and required keys, app icon (1024 px, no alpha), workflow YAML,
  Swift files referenced and not empty, generated Xcode files not committed
"""
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios"
errors: list[str] = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def python_tests():
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    check(result.wasSuccessful(), f"Python tests failed ({len(result.failures)} failures, {len(result.errors)} errors)")


def ios_project():
    try:
        import yaml
    except ImportError:
        errors.append("pyyaml not installed (pip install pyyaml)")
        return
    spec = yaml.safe_load((IOS / "project.yml").read_text(encoding="utf-8"))
    app = spec["targets"]["TrekPad"]
    for t in spec["targets"].values():
        for src in t.get("sources", []):
            path = src["path"] if isinstance(src, dict) else src
            check((IOS / path).exists(), f"project.yml source path missing: ios/{path}")
    info = app["info"]["properties"]
    for key in ("NSBluetoothAlwaysUsageDescription", "NSHealthUpdateUsageDescription", "NSHealthShareUsageDescription"):
        check(str(info.get(key, "")).strip(), f"Info.plist key missing or empty: {key}")
    check("bluetooth-central" in info.get("UIBackgroundModes", []), "UIBackgroundModes needs bluetooth-central")
    check(app["entitlements"]["properties"].get("com.apple.developer.healthkit") is True, "HealthKit entitlement missing")
    check(app["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"] == "com.welshmandesign.trekpad", "bundle id changed")
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        try:
            yaml.safe_load(wf.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{wf.name}: invalid YAML: {e}")


def ios_files():
    icon = IOS / "App/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
    if icon.exists():
        head = icon.read_bytes()[:26]
        w, h = struct.unpack(">II", head[16:24])
        color_type = head[25]
        check((w, h) == (1024, 1024), f"app icon is {w}x{h}, needs 1024x1024")
        check(color_type in (0, 2), "app icon has an alpha channel (App Store rejects it)")
    else:
        errors.append("app icon missing")
    for swift in list((IOS / "App").rglob("*.swift")) + list((IOS / "Tests").rglob("*.swift")):
        text = swift.read_text(encoding="utf-8")
        check(text.strip(), f"empty Swift file: {swift.relative_to(ROOT)}")
        check(text.count("{") == text.count("}"), f"unbalanced braces in {swift.relative_to(ROOT)}")
    tracked = subprocess.run(["git", "ls-files", "ios"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    for f in tracked:
        check(not (f.startswith("ios/TrekPad.xcodeproj") or (f.startswith("ios/Support/")
                   and (f.endswith(".plist") or f.endswith(".entitlements")))),
              f"generated file is committed (edit project.yml instead): {f}")


if __name__ == "__main__":
    python_tests()
    ios_project()
    ios_files()
    if errors:
        print("\nPREFLIGHT FAILED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("\npreflight OK")
