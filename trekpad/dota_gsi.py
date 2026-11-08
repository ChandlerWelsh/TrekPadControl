"""Dota 2 game events through Game State Integration (GSI).

Dota 2 officially sends live game state to a local address when a gamestate_integration_*.cfg
is in game\\dota\\cfg\\gamestate_integration and the game runs with the `-gamestateintegration`
launch option. TrekPad receives it at POST /api/gsi/dota2 (checked against its own token, which
is separate from the belt-control token because it's stored in Dota's folder).

From each update we use: map.game_state (match phase), map.clock_time / map.paused, hero.alive /
hero.respawn_seconds, player kills / deaths / assists / last_hits. Spectating (no player stats)
doesn't count as playing.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import steam

log = logging.getLogger(__name__)

# Heroes are on the map from the pre-game horn countdown until the game is over.
IN_MATCH_STATES = {"DOTA_GAMERULES_STATE_PRE_GAME", "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"}
STALE_S = 90.0          # Dota sends a heartbeat every 30 s; silence this long = game closed
CFG_NAME = "gamestate_integration_trekpad.cfg"


def find_dota() -> Path | None:
    return steam.game_dir("dota 2 beta", "game/dota")


def cfg_text(port: int, token: str) -> str:
    return f'''"TrekPad"
{{
    "uri"           "http://127.0.0.1:{port}/api/gsi/dota2"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.5"
    "heartbeat"     "30.0"
    "data"
    {{
        "provider"  "1"
        "map"       "1"
        "player"    "1"
        "hero"      "1"
    }}
    "auth"
    {{
        "token"     "{token}"
    }}
}}
'''


def install_config(port: int, token: str, dota_dir: Path | None = None) -> Path | None:
    """Write (or update) TrekPad's GSI config in Dota's folder. Returns its path, or None."""
    d = dota_dir or find_dota()
    if d is None:
        return None
    path = d / "game" / "dota" / "cfg" / "gamestate_integration" / CFG_NAME
    text = cfg_text(port, token)
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            log.info("wrote Dota 2 GSI config %s", path)
    except OSError as e:
        log.warning("couldn't write Dota 2 GSI config: %s", e)
        return None
    return path


class DotaGSI:
    """Turns GSI updates into game mode events ("match_start", "death", "respawn", "match_end")
    and keeps stats for the Dota 2 match card."""

    GAME = "dota2"

    def __init__(self, on_event=None, clock=time.time):
        self.on_event = on_event or (lambda e: None)
        self._clock = clock
        self._lock = threading.Lock()
        self.last_seen = 0.0
        self.in_match = False
        self.dead = False
        self.dead_since: float | None = None
        self.time_dead_s = 0.0
        self.started_at: float | None = None
        self.stats: dict = {}
        self.last_match: dict | None = None

    @property
    def available(self) -> bool:
        return self.last_seen > 0 and self._clock() - self.last_seen < STALE_S

    def feed(self, body: dict) -> list[str]:
        if not isinstance(body, dict):
            return []
        with self._lock:
            events = self._feed(body)
        for ev in events:
            log.info("dota 2: %s", ev)
            try:
                self.on_event(ev)
            except Exception:
                log.exception("dota event handler failed")
        return events

    def _feed(self, body: dict) -> list[str]:
        now = self._clock()
        self.last_seen = now
        m = body.get("map") or {}
        player = body.get("player") or {}
        hero = body.get("hero") or {}
        playing = isinstance(player, dict) and "kills" in player and isinstance(hero, dict)
        in_match = playing and m.get("game_state") in IN_MATCH_STATES
        events: list[str] = []

        if in_match and not self.in_match:
            self.in_match, self.dead, self.dead_since, self.time_dead_s = True, False, None, 0.0
            self.started_at = now
            events.append("match_start")
        elif not in_match and self.in_match:
            self._close_death(now)
            s = self.stats
            self.last_match = {"ended_at": now, "duration_s": max(0, int(s.get("clock_time") or 0)),
                               "kills": s.get("kills", 0), "deaths": s.get("deaths", 0),
                               "assists": s.get("assists", 0), "time_dead_s": round(self.time_dead_s),
                               "won": self._won(m, player)}
            self.in_match, self.dead = False, False
            events.append("match_end")

        if in_match:
            alive = bool(hero.get("alive", True))
            if not alive and not self.dead:
                self.dead, self.dead_since = True, now
                events.append("death")
            elif alive and self.dead:
                self.dead = False
                self._close_death(now)
                events.append("respawn")
            respawn = int(hero.get("respawn_seconds") or 0)
            self.stats = {
                "hero": str(hero.get("name") or "").replace("npc_dota_hero_", "").replace("_", " ").title(),
                "level": hero.get("level"),
                "kills": int(player.get("kills") or 0), "deaths": int(player.get("deaths") or 0),
                "assists": int(player.get("assists") or 0), "last_hits": int(player.get("last_hits") or 0),
                "clock_time": m.get("clock_time"), "clock_at": now, "paused": bool(m.get("paused")),
                "respawn_at": now + respawn if not alive and respawn > 0 else None,
            }
        return events

    @staticmethod
    def _won(m: dict, player: dict):
        win, team = m.get("win_team"), player.get("team_name")
        if win in ("radiant", "dire") and team in ("radiant", "dire"):
            return win == team
        return None

    def _close_death(self, now: float):
        if self.dead_since is not None:
            self.time_dead_s += max(0.0, now - self.dead_since)
            self.dead_since = None

    def info(self) -> dict | None:
        """Match state and stats for game mode and the match card; None while Dota isn't sending."""
        if not self.available:
            return None
        with self._lock:
            return {"game": self.GAME, "in_match": self.in_match, "started_at": self.started_at,
                    "dead": self.dead, "dead_since": self.dead_since, "time_dead_s": round(self.time_dead_s),
                    **self.stats, "last_match": self.last_match}
