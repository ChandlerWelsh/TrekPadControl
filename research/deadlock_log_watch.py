"""Live check of Deadlock death/respawn detection from console.log (needs -condebug).

    python research\\deadlock_log_watch.py

Prints each event with the time, and beeps so you can check without leaving the game:
death = low tone, respawn = high tone, match start/end = two tones. Ctrl+C to stop.
"""
import sys
import threading
import time
import winsound
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trekpad.deadlock_log import DeadlockLogWatcher  # noqa: E402

TONES = {"death": [(330, 350)], "respawn": [(880, 250)],
         "match_start": [(523, 120), (784, 160)], "match_end": [(784, 120), (523, 160)]}
dead_at = [None]
counts: dict[str, int] = {}


def on_event(ev: str):
    counts[ev] = counts.get(ev, 0) + 1
    extra = ""
    if ev == "death":
        dead_at[0] = time.time()
    elif ev == "respawn" and dead_at[0]:
        extra = f"  (dead for {time.time() - dead_at[0]:.0f} s)"
    print(f"{time.strftime('%H:%M:%S')}  {ev.upper()}{extra}", flush=True)
    threading.Thread(target=lambda: [winsound.Beep(f, d) for f, d in TONES[ev]], daemon=True).start()


w = DeadlockLogWatcher(on_event)
print(f"Watching {w.path}\nDie and respawn in game; each event prints here and beeps. Ctrl+C to stop.\n", flush=True)
w.start()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nSeen:", ", ".join(f"{k} x{v}" for k, v in counts.items()) or "nothing")
