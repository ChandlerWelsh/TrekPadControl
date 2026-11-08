"""Does Deadlock send Game State Integration (GSI) data? A one-off test.

Valve's Dota 2 and CS2 send live game state to a local web address when a
gamestate_integration_*.cfg file is in the game's cfg folder. Deadlock has no documentation
for this, so this script finds out:

  1. writes a GSI config into Deadlock's cfg folder (game\\citadel\\cfg\\gamestate_integration)
  2. listens on http://127.0.0.1:<port>/ and saves everything Deadlock sends to gsi_capture.jsonl
  3. removes the config again when you press Ctrl+C (unless --keep)

Run it from a terminal (needs only Python):

    python research\\gsi_test.py
    python research\\gsi_test.py --game-dir "D:\\SteamLibrary\\steamapps\\common\\Deadlock"
    python research\\gsi_test.py --remove      # just delete the config

Nothing is sent anywhere else: the listener only accepts connections from this PC.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CFG_NAME = "gamestate_integration_trekpad_test.cfg"
DATA_KEYS = ["provider", "map", "player", "hero", "abilities", "items", "allplayers", "draft", "wearables",
             "buildings", "league", "couriers", "neutralitems", "events", "minimap", "roshan",
             "player_id", "player_state", "player_match_stats", "player_weapons", "round", "bomb", "phase_countdowns"]


def steam_library_dirs() -> list[Path]:
    """Steam install folder (from the registry) plus every library listed in libraryfolders.vdf."""
    roots: list[Path] = []
    try:
        import winreg
        for hive, key, value in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:  # not Windows
        pass
    roots += [Path(r"C:\Program Files (x86)\Steam"), Path.home() / ".local/share/Steam"]
    libs: list[Path] = []
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        libs.append(root)
        if vdf.exists():
            libs += parse_library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
    seen, out = set(), []
    for p in libs:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def parse_library_folders(text: str) -> list[Path]:
    return [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]


def find_deadlock(game_dir: str | None) -> Path | None:
    candidates = [Path(game_dir)] if game_dir else [lib / "steamapps" / "common" / "Deadlock"
                                                    for lib in steam_library_dirs()]
    for d in candidates:
        if (d / "game" / "citadel").is_dir():
            return d
    return None


def cfg_text(port: int, token: str) -> str:
    data = "\n".join(f'        "{k}" "1"' for k in DATA_KEYS)
    return f'''"TrekPad GSI test"
{{
    "uri" "http://127.0.0.1:{port}/"
    "timeout" "5.0"
    "buffer" "0.1"
    "throttle" "0.5"
    "heartbeat" "10.0"
    "auth"
    {{
        "token" "{token}"
    }}
    "data"
    {{
{data}
    }}
}}
'''


def key_paths(obj, prefix="", depth=0, out=None) -> set[str]:
    """Flatten JSON keys (to 3 levels) so you can see which fields exist."""
    out = set() if out is None else out
    if isinstance(obj, dict) and depth < 3:
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.add(p)
            key_paths(v, p, depth + 1, out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", help=r"Deadlock folder, e.g. D:\SteamLibrary\steamapps\common\Deadlock")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", default="gsi_capture.jsonl", help="where to save what arrives")
    ap.add_argument("--keep", action="store_true", help="leave the config in place on exit")
    ap.add_argument("--remove", action="store_true", help="only remove the config and exit")
    args = ap.parse_args()

    deadlock = find_deadlock(args.game_dir)
    if deadlock is None:
        print("Couldn't find Deadlock. Pass its folder with --game-dir, for example:")
        print(r'  python research\gsi_test.py --game-dir "D:\SteamLibrary\steamapps\common\Deadlock"')
        print("(In Steam: right-click Deadlock > Manage > Browse local files shows the folder.)")
        sys.exit(1)
    cfg_dir = deadlock / "game" / "citadel" / "cfg" / "gamestate_integration"
    cfg_path = cfg_dir / CFG_NAME

    if args.remove:
        if cfg_path.exists():
            cfg_path.unlink()
            print(f"Removed {cfg_path}")
        else:
            print("Nothing to remove.")
        return

    token = secrets.token_hex(8)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(cfg_text(args.port, token), encoding="utf-8")
    out_path = Path(args.out).resolve()
    stats = {"count": 0, "bad_auth": 0, "first": None, "keys": set()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            self.send_response(200)
            self.end_headers()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                print(f"  received {len(raw)} bytes that aren't JSON")
                return
            auth = (body.get("auth") or {}).get("token") if isinstance(body, dict) else None
            if auth != token:
                stats["bad_auth"] += 1
                return
            stats["count"] += 1
            stats["first"] = stats["first"] or datetime.now()
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"received": datetime.now().isoformat(timespec="seconds"), "body": body}) + "\n")
            new = sorted(key_paths(body) - stats["keys"] - {"auth", "auth.token"})
            stats["keys"] |= key_paths(body)
            if stats["count"] == 1:
                print("\n  *** Deadlock sent game state! It does support GSI. ***\n")
            tops = ", ".join(k for k in body if k != "auth")
            print(f"  #{stats['count']} at {datetime.now():%H:%M:%S}: {tops}")
            for k in new[:40]:
                print(f"      new field: {k}")

    try:
        server = HTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"Port {args.port} is busy. Try another, e.g. --port 8767")
        cfg_path.unlink(missing_ok=True)
        sys.exit(1)
    server.timeout = 1.0

    print(f"Deadlock folder:  {deadlock}")
    print(f"Wrote config:     {cfg_path}")
    print(f"Listening on:     http://127.0.0.1:{args.port}/")
    print(f"Saving data to:   {out_path}")
    print("""
Now:
  1. In Steam: right-click Deadlock > Properties > Launch options, add:  -gamestateintegration
     (Dota 2 only sends GSI with this option; Deadlock may need it too.)
  2. Start Deadlock. If it was already running, quit and start it again (configs load at launch).
  3. Play for a few minutes: a bot match or the sandbox. Dying once and buying an item helps.
  4. Come back here and press Ctrl+C.
""")
    started = time.time()
    try:
        while True:
            server.handle_request()
            waited = int(time.time() - started)
            if stats["count"] == 0 and waited and waited % 120 == 0:
                print(f"  ...nothing yet after {waited // 60} min")
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if not args.keep:
            cfg_path.unlink(missing_ok=True)
            print("\nRemoved the test config.")

    print("\nResult:")
    if stats["count"]:
        print(f"  Deadlock sent {stats['count']} updates. Saved to {out_path}")
        print("  Fields seen:")
        for k in sorted(stats["keys"] - {"auth", "auth.token"}):
            print(f"    {k}")
        print("\n  Share gsi_capture.jsonl (or the field list) and game mode can use it.")
    else:
        print("  Nothing arrived. Unless Deadlock wasn't restarted or the launch option was missing,")
        print("  Deadlock doesn't support GSI (yet). Game mode keeps using idle detection, and an")
        print("  Overwolf bridge is the next option.")
    if stats["bad_auth"]:
        print(f"  ({stats['bad_auth']} requests arrived with the wrong token and were ignored.)")


if __name__ == "__main__":
    main()
