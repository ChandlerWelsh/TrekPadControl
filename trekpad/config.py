"""Settings stored in %APPDATA%\\TrekPadControl\\config.json."""
import json
import os
import secrets
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "TrekPadControl"
CONFIG_PATH = APP_DIR / "config.json"

DEFAULTS = {
    "address": "",                 # filled in automatically the first time the pad is found
    "max_speed_mph": 4.0,          # app never asks for more than this (pad max is 4.0)
    "theme": "system",             # system | light | dark
    "speed_step_mph": 0.1,
    "incline_step_pct": 1,
    "speed_presets_mph": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "incline_presets_pct": [0, 2, 4, 6, 8, 10, 12],
    "api_port": 8765,
    "api_token": "",               # generated on first run
    "min_workout_seconds": 60,     # shorter sessions aren't saved
    "workout_end_idle_seconds": 90,  # belt stopped this long -> workout finished
    "calorie_source": "weight",    # weight: work calories out from weight_lb | pad: use the pad's number
    "weight_lb": 187,
    "game_mode": {
        "enabled": False,
        "profile": "both",         # aim: speed follows play | climb: incline rises over time | both
        "games": ["deadlock.exe", "project8.exe"],  # apps that count as a game when in front
        "easy_mph": 2.0,           # while you're playing (aiming)
        "hard_mph": 2.8,           # while you're idle or dead
        "start_incline_pct": 0,
        "max_incline_pct": 4,
        "climb_every_min": 5,      # +1% incline this often while in game
        "idle_seconds": 8,         # no mouse/keyboard this long counts as idle (dead, shopping)
        "sounds": True,            # beep before speeding up or changing incline
    },
    "hotkeys": {
        "speed_up": "ctrl+alt+page_up",
        "speed_down": "ctrl+alt+page_down",
        "incline_up": "ctrl+alt+shift+page_up",
        "incline_down": "ctrl+alt+shift+page_down",
        "start": "ctrl+alt+home",
        "stop": "ctrl+alt+end",
        "game_mode": "ctrl+alt+g",
    },
}


def load() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    cfg = {**DEFAULTS, **data}
    cfg["hotkeys"] = {**DEFAULTS["hotkeys"], **data.get("hotkeys", {})}
    cfg["game_mode"] = {**DEFAULTS["game_mode"], **data.get("game_mode", {})}
    if not cfg.get("api_token"):
        cfg["api_token"] = secrets.token_urlsafe(18)
    save(cfg)
    return cfg


def save(cfg: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
