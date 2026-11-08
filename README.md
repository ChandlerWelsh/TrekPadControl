# TrekPad Control

A small Windows app for controlling a **NovaWalk W50 TrekPad** walking pad (Merach, Bluetooth
name `MRK-T25-xxxx`) from a PC instead of the phone app. It can:

- **Start, stop, and change speed and incline** from a window, global keyboard shortcuts, or a
  local HTTP API
- **Show live stats**: speed, incline, time, distance and calories
- **Record each walk** and **sync it to Apple Health** through an iPhone Shortcut
- **Game mode**: speed and incline follow what you're doing in a game such as Deadlock
- **Fix a firmware bug** where changing incline also raises the speed (see [Safety](#safety))

No reverse engineering of the Merach app was needed. The pad speaks standard Bluetooth FTMS
(Fitness Machine Service), with a couple of quirks documented in
[research/FINDINGS.md](research/FINDINGS.md).

> **Unofficial.** Not affiliated with Merach. Use at your own risk, and keep the pad's remote
> within reach.

## Download (no Python needed)

1. Get **`TrekPad-windows.zip`** from the [latest release](https://github.com/ChandlerWelsh/TrekPadControl/releases/latest).
2. Unzip it anywhere (for example `%LOCALAPPDATA%\Programs\TrekPad`) and run **`TrekPad.exe`**.
   Pin it to Start or the taskbar if you like.
3. The exe isn't code-signed, so Windows SmartScreen may warn the first time: choose
   *More info → Run anyway*.

Needs Windows 10/11 with Bluetooth LE (most built-in Intel/Realtek adapters work) and the WebView2
runtime (built into Windows 11). Close the Merach phone app first, or turn off the phone's
Bluetooth: the pad accepts one connection at a time. Closing the window quits the app completely.

## Run from source

Needs Python 3.11+ ([python.org](https://www.python.org/downloads/)).

```powershell
git clone https://github.com/ChandlerWelsh/TrekPadControl.git
cd TrekPadControl
python -m pip install -r requirements.txt
```

Double-click **`TrekPad.pyw`** to start it without a console window, or run
`python -m trekpad.main` to see log output.

On first run it scans for a device named `MRK-…` that offers FTMS, then remembers its address.
Settings, the workout database and the log live in `%APPDATA%\TrekPadControl\`.

The first time it runs, Windows may ask whether Python can use the network. Allow it on
**private networks** so your iPhone can reach it for Apple Health sync.

## Using it

- **Speed gauge:** click (or drag and release) anywhere on the ring to jump to that speed. Hovering
  shows the speed you'd get before you click.
- **Incline bars:** click a bar to go straight to that incline (0–12%).
- **− / +** buttons fine-tune by 0.1 mph or 1%.
- **Desk strip:** the icon next to minimize shrinks the app to a slim bar that stays on top of your
  other windows. The arrows icon on the strip expands it again.
- **Settings** (gear icon): theme (system, light or dark), max speed, game mode, calories, the phone
  link, and the Apple Health address and token.
- **Phone remote:** *Settings → Copy phone link*, then open it on your phone (same Wi-Fi). The same
  interface runs in the phone's browser.

| Action | Window | Global shortcut (any app) |
|---|---|---|
| Start | **Start** | `Ctrl+Alt+Home` |
| **Stop** | **Stop**, `Space` or `Esc` | `Ctrl+Alt+End` |
| Speed | click the gauge, − / +, `↑` / `↓` | `Ctrl+Alt+PgUp` / `PgDn` (±0.1) |
| Incline | click a bar, − / +, `→` / `←` | `Ctrl+Alt+Shift+PgUp` / `PgDn` (±1) |
| Game mode on/off | the **Game** button next to the gear | `Ctrl+Alt+G` |

Shortcuts, steps, presets, the max speed and your weight can be changed in `%APPDATA%\TrekPadControl\config.json`
(restart the app afterwards). I avoided `Ctrl+Alt+arrows` because some Intel graphics drivers use
them to rotate the screen.

Speed and incline commands only work while the belt is running. Press Start first.

## Safety

- **Incline bug workaround:** on this firmware, every incline change makes the pad multiply its
  speed by about 1.6 (1.0 → 1.6 → 2.5 mph…). After each incline change made *from this app*, it
  waits for the pad to confirm the incline and then resends your speed, which usually lands within
  about 1.5 s. It keeps watching for 5 seconds and stops the belt if the speed won't settle.
  Changes made with the pad's own remote aren't affected by this logic.
- **Max speed:** the app never asks for more than the max speed you set, and pulls the speed back
  if the remote goes above it.
- **Closing the app** while the belt is moving asks whether to stop the belt first.
- **If the app crashes or Bluetooth drops,** the pad keeps going at its current speed, so use the
  remote. The app reconnects automatically.

## Game mode

Speed and incline adjust by themselves while you play. Turn it on with the **Game** button (next to
the gear) or `Ctrl+Alt+G`, and set it up in *Settings → Game mode*. Start the belt yourself first:
game mode never starts it.

| Profile | What it does |
|---|---|
| **Pace** | Easy pace (default 2.0 mph) while you're playing, hard pace (2.8 mph) while you're idle or dead. Aiming suffers at speed, so the hard part lands when you aren't aiming. |
| **Climb** | The incline rises 1% every 5 minutes in game, from 0% up to a max (default 4%). |
| **Both** (default) | Pace and Climb together. |

How it knows what you're doing (no game data needed):

- **Which app is in front.** It only works while a game from the list is in front (`deadlock.exe`
  by default). *Settings → Games* has an *Add* button for the last app you used, so switch to the
  game, back to TrekPad, and add it. A quick alt-tab (under 5 s) doesn't count as leaving.
- **Mouse and keyboard activity.** No input for 8 seconds counts as idle: usually dead, shopping or
  waiting. Windows only reports the time of the last input. No keys are read or recorded.
  Controller input doesn't count.

What it does to stay safe:

- Changes speed 0.1 mph at a time: every 2 s going up, every 1 s coming down. Easy to hard takes
  about 16 seconds.
- **Beeps**, then waits 3 seconds, before speeding up or changing the incline.
- Changes the incline only at a moment you aren't aiming (idle, dead, or out of the game), or
  after a minute of waiting. Every incline change makes this pad's speed jump briefly (see
  [Safety](#safety)), so you don't want one mid-fight.
- Never goes above your max speed, and does nothing while the belt is stopped.
- If you change the **speed** yourself (app, shortcut or remote), it steps back for a minute. If you
  change the **incline**, the climb carries on from your incline.
- Leaving the game eases back to the easy pace. After 2 minutes away, the climb starts over.

The stop shortcut (`Ctrl+Alt+End`) and the remote work as always.

More settings in `config.json` under `game_mode`: `games`, `easy_mph`, `hard_mph`,
`start_incline_pct`, `max_incline_pct`, `climb_every_min`, `idle_seconds` and `sounds`.

### Game events (optional)

Anything on your PC can tell game mode what's happening in the game, which beats guessing from
idle time:

```powershell
curl.exe -X POST -H "X-Token: <token>" "http://localhost:8765/api/game/event?event=death"
```

Events: `death` (hard pace until `respawn`, or 90 s), `respawn`, `match_start` and `match_end`
(both restart the climb). This is where Game State Integration or an Overwolf bridge would plug in.

### Does Deadlock support Game State Integration?

Valve hasn't documented it for Deadlock. To test it on your PC (needs Python):

```powershell
python research\gsi_test.py
```

The script finds Deadlock, writes a GSI config into `game\citadel\cfg\gamestate_integration`, and
listens for data. Add the `-gamestateintegration` launch option in Steam, start Deadlock (restart it
if it's open), play a few minutes, then press Ctrl+C. It reports whether anything arrived and which
fields, saves the data to `gsi_capture.jsonl`, and removes the config. If Deadlock isn't found, pass
`--game-dir "<path to steamapps\common\Deadlock>"`.

## Calories

Choose where the calorie number comes from in *Settings → Calories*, or in `config.json`:

| Setting | Default | |
|---|---|---|
| `calorie_source` | `"weight"` | `"weight"`: work it out from your weight. `"pad"`: use the pad's own number |
| `weight_lb` | `187` | Your weight in pounds (also adjustable with − / + in Settings) |

**From your weight** uses the ACSM walking equation with the pad's measured distance and the incline
at each moment: about 0.1 ml O₂ per kg per metre on the flat, plus 1.8 × grade per metre uphill,
at 5 kcal per litre of O₂. It leaves out resting metabolism, because Apple Health already logs that
as *Resting Energy*, so the number matches Health's **Active Energy**. At 187 lb that's about
68 kcal per flat mile, and more on an incline. The pad doesn't know your weight, so its own number
is only a rough guess.

The live Calories tile, the saved walks and the Shortcut all use the chosen number. Each saved walk
also keeps the pad's number as `pad_kcal`, so you can compare the two.

## Apple Health sync (iPhone Shortcut)

Apple Health only accepts data from apps on your iPhone or Watch, so the PC saves each walk and an
iPhone Shortcut copies new ones into Health. Your iPhone and PC need to be on the **same Wi-Fi**.

A walk is saved when the belt has been stopped for 90 seconds (a short break stays part of the
same walk). Walks under 60 seconds aren't saved.

### Build the Shortcut (one time)

In the window, use **Copy address** and **Copy token**, and send them to your phone (for example
with a note or email to yourself). Then in the **Shortcuts** app create a new shortcut named
*Log TrekPad Walks*:

1. **Get Contents of URL**
   - URL: `<address>/api/workouts?unsynced=1` (for example `http://192.168.50.44:8765/api/workouts?unsynced=1`)
   - Method: GET. Headers: `X-Token` = `<token>`
2. **Get Dictionary Value**: get the value for key `workouts` in *Contents of URL*
3. **Repeat with Each** item in *Dictionary Value*:
   1. **Get Dictionary Value**: `start_local` from *Repeat Item*, then **Get Dates from Input**
      on it (it becomes a date)
   2. **Get Dictionary Value**: `duration_min`, `distance_mi` and `kcal` from *Repeat Item*
      (three actions, or use the Repeat Item's magic variable directly)
   3. **Log Workout** (Health): Type **Walking**, Start Date = *Dates*, Duration = `duration_min`
      minutes, Distance = `distance_mi` miles, Active Energy = `kcal` kcal. Turn on any of these
      that are hidden under *Show More*. The first run asks for Health permission.
   4. **Get Contents of URL**: `<address>/api/workouts/` + *Repeat Item → id* + `/synced`.
      Method **POST**, header `X-Token` = `<token>`. This marks the walk as done so it's never
      logged twice.
4. *(optional)* **Show Notification**: "Logged *Count of Dictionary Value* walk(s) to Health"

The first time it runs, iOS asks to let Shortcuts find devices on your **local network**. Allow it.

### Run it automatically

- **Shortcuts → Automation → + → Time of Day** (for example 9:00 PM daily) → *Run Immediately* →
  *Log TrekPad Walks*. It catches up on any walks that haven't synced yet.
- Or **NFC**: put a cheap NFC sticker on the pad and make an NFC automation, so you tap your phone
  on it when you finish.
- Or add the shortcut to your Home Screen or a widget.

### Apple Watch

If you **start an Indoor Walk workout on your Watch** while walking, Health will show the same walk
twice: the Watch's workout and the pad's. Pick one approach:

- **Let the pad log it (recommended).** Don't start a Watch workout. The pad's treadmill distance is
  more accurate than arm-swing estimates, and the Watch still records background heart rate, which
  Health shows during the logged workout.
- **Let the Watch log it.** Start an Indoor Walk on the Watch for continuous heart rate and rings
  credit, and don't run the Shortcut, or delete the Log Workout step.

## Local HTTP API

The app listens on port `8765` on your local network. Every `/api` call needs the token, either as
header `X-Token: <token>` or as `?token=<token>`. Control commands are `POST`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/status` | live state, connection, workouts waiting |
| POST | `/api/start`, `/api/stop` | |
| POST | `/api/speed?mph=2.0` | 0.6–max |
| POST | `/api/speed/up`, `/api/speed/down` | optional `?step=0.2` |
| POST | `/api/incline?pct=4` | 0–12 |
| POST | `/api/incline/up`, `/api/incline/down` | optional `?step=2` |
| GET | `/api/workouts` | `?unsynced=1` for only walks not yet in Health |
| POST | `/api/workouts/<id>/synced` | mark as logged |
| GET/POST | `/api/game` | game mode status; POST `enabled`, `profile`, `easy_mph`, `hard_mph`, `max_incline_pct`, `add_game`, `remove_game`… |
| POST | `/api/game/event?event=death` | `death`, `respawn`, `match_start`, `match_end` |

```powershell
curl.exe -X POST -H "X-Token: <token>" "http://localhost:8765/api/speed?mph=2.0"
```

This is plain HTTP for your home network. Anyone on your Wi-Fi who has the token can control the
pad, so don't forward the port to the internet. To change the token, delete `api_token` from
`config.json` and restart.

## Development

```powershell
python -m unittest discover -s tests -t .
```

Build the exe (output in `dist\TrekPad\`). Add `-Install` to copy it to
`%LOCALAPPDATA%\Programs\TrekPad` and add Start Menu and desktop shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Install
```

- `trekpad/ftms.py`: protocol (parsing and commands, quirks documented at the top)
- `trekpad/pad.py`: Bluetooth connection, commands, incline-bug workaround, speed watchdog
- `trekpad/workouts.py`: walk recording, storage and calorie calculation
- `trekpad/gamemode.py`: game mode (signals, profiles, ramping and safety rules)
- `trekpad/ui/`: the interface (HTML/CSS/JS), served by the local server and shown in a frameless
  pywebview (Edge WebView2) window
- `trekpad/api.py`, `trekpad/hotkeys.py`, `trekpad/main.py`
- `research/`: the scan, GATT-dump and live-test scripts used to work out the protocol, and
  `gsi_test.py` for checking Deadlock's Game State Integration

## License

MIT
