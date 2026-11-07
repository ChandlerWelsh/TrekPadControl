"""Claude Code PreToolUse hook: run scripts/preflight.py before any `git push`, so a
5-10 minute CI round isn't wasted on something checkable locally."""
import json
import subprocess
import sys
from pathlib import Path

event = json.load(sys.stdin)
command = str(event.get("tool_input", {}).get("command", ""))
if "git push" not in command:
    sys.exit(0)
root = Path(__file__).resolve().parents[2]
r = subprocess.run([sys.executable, str(root / "scripts" / "preflight.py")], cwd=root, capture_output=True, text=True)
if r.returncode != 0:
    print("Preflight failed, push blocked:\n" + (r.stdout + r.stderr)[-3000:], file=sys.stderr)
    sys.exit(2)
sys.exit(0)
