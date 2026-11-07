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
    "hotkeys": {
        "speed_up": "ctrl+alt+page_up",
        "speed_down": "ctrl+alt+page_down",
        "incline_up": "ctrl+alt+shift+page_up",
        "incline_down": "ctrl+alt+shift+page_down",
        "start": "ctrl+alt+home",
        "stop": "ctrl+alt+end",
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
    if not cfg.get("api_token"):
        cfg["api_token"] = secrets.token_urlsafe(18)
    save(cfg)
    return cfg


def save(cfg: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
