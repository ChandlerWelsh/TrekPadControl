"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);
  const MIN = 0.6, MAX = 4.0;                 // pad speed range, mph
  const CX = 130, CY = 124, R = 100;          // gauge geometry (SVG units)
  const A0 = 135, SPAN = 270;                 // arc starts bottom-left, sweeps 270° clockwise
  const NS = "http://www.w3.org/2000/svg";
  const METERS_PER_MILE = 1609.344;

  // ---- token: from #token=… (window and phone link), remembered for reloads
  const hash = new URLSearchParams(location.hash.slice(1));
  let token = hash.get("token") || "";
  try {
    if (token) localStorage.setItem("tp_token", token);
    else token = localStorage.getItem("tp_token") || "";
  } catch (_) { /* storage blocked: token only lives in memory */ }
  if (hash.get("token")) history.replaceState(null, "", location.pathname);

  let S = { connected: false, status: "Connecting…", speed_mph: 0, incline_pct: 0, running: false };
  let settings = { theme: "system", max_speed_mph: 4, speed_step_mph: 0.1, incline_step_pct: 1 };
  let speedPreview = null, inclinePreview = null, pending = { speed: null, incline: null };
  let lastMsgAt = Date.now() / 1000, lastAskClose = Date.now() / 1000, desk = null, failures = 0;

  // ---- API
  async function api(method, path, body) {
    const r = await fetch(path, {
      method, headers: { "X-Token": token, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
    return j;
  }
  const post = (path, body) => api("POST", path, body).catch((e) => { toast(e.message); throw e; });

  // ---- toast
  let toastTimer;
  function toast(text) {
    const t = $("#toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  // ---- theme
  const darkQuery = matchMedia("(prefers-color-scheme: dark)");
  function applyTheme() {
    const t = settings.theme;
    const dark = t === "dark" || (t === "system" && darkQuery.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    $$("#theme-seg button").forEach((b) => b.classList.toggle("sel", b.dataset.theme === t));
  }
  darkQuery.addEventListener("change", applyTheme);

  // ---- gauge geometry
  const clampSpeed = (v) => Math.min(Math.max(v, MIN), MAX);
  const angleOf = (v) => A0 + ((clampSpeed(v) - MIN) / (MAX - MIN)) * SPAN;
  const pt = (a, r = R) => { const t = (a * Math.PI) / 180; return [CX + r * Math.cos(t), CY + r * Math.sin(t)]; };
  function arc(a1, a2, r = R) {
    if (a2 - a1 < 0.5) return "";
    const [x1, y1] = pt(a1, r), [x2, y2] = pt(a2, r);
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${a2 - a1 > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  }
  function buildGauge() {
    const full = arc(A0, A0 + SPAN);
    $("#g-track").setAttribute("d", full);
    $("#g-hit").setAttribute("d", full);
    const ticks = $("#g-ticks");
    for (let v = 1; v <= 4.001; v += 0.5) {
      const major = Math.abs(v - Math.round(v)) < 0.01;
      const a = angleOf(v), [x1, y1] = pt(a, R - 15), [x2, y2] = pt(a, R - (major ? 24 : 20));
      const line = document.createElementNS(NS, "line");
      line.setAttribute("x1", x1); line.setAttribute("y1", y1); line.setAttribute("x2", x2); line.setAttribute("y2", y2);
      line.setAttribute("class", major ? "g-tick major" : "g-tick");
      ticks.appendChild(line);
      if (major) {
        const [lx, ly] = pt(a, R + 20);
        const text = document.createElementNS(NS, "text");
        text.setAttribute("x", lx); text.setAttribute("y", ly); text.setAttribute("class", "g-label");
        text.textContent = String(Math.round(v));
        ticks.appendChild(text);
      }
    }
  }

  function speedFromPointer(e) {
    const svg = $("#gauge"), p = svg.createSVGPoint();
    p.x = e.clientX; p.y = e.clientY;
    const q = p.matrixTransform(svg.getScreenCTM().inverse());
    let a = (Math.atan2(q.y - CY, q.x - CX) * 180) / Math.PI;
    a = (a - A0 + 720) % 360;
    if (a > SPAN) a = a > SPAN + (360 - SPAN) / 2 ? 0 : SPAN;   // the gap at the bottom snaps to the nearest end
    return Math.round((MIN + (a / SPAN) * (MAX - MIN)) * 10) / 10;
  }
  const limitSpeed = (v) => Math.min(v, settings.max_speed_mph);

  // ---- commands
  function needsRunning() {
    if (!S.connected) { toast("The pad isn't connected. Check that it's on and nearby."); return true; }
    if (!S.running) { toast("Press Start first."); return true; }
    return false;
  }
  function setSpeed(v) {
    if (needsRunning()) return;
    if (v > settings.max_speed_mph) toast(`Your max speed is ${settings.max_speed_mph.toFixed(1)} mph`);
    v = Math.round(clampSpeed(limitSpeed(v)) * 10) / 10;
    pending.speed = { v, until: Date.now() + 5000 };
    render();
    post("/api/speed", { mph: v }).catch(() => { pending.speed = null; render(); });
  }
  function setIncline(p) {
    if (needsRunning()) return;
    pending.incline = { v: p, until: Date.now() + 6000 };
    render();
    post("/api/incline", { pct: p }).catch(() => { pending.incline = null; render(); });
  }
  const ACTIONS = {
    "speed-down": () => { if (!needsRunning()) post("/api/speed/down").catch(() => {}); },
    "speed-up": () => { if (!needsRunning()) post("/api/speed/up").catch(() => {}); },
    "incline-down": () => { if (!needsRunning()) post("/api/incline/down").catch(() => {}); },
    "incline-up": () => { if (!needsRunning()) post("/api/incline/up").catch(() => {}); },
    stop: () => post("/api/stop").catch(() => {}),
    start: () => {
      if (!S.connected) return toast("The pad isn't connected. Check that it's on and nearby.");
      post("/api/start").then(() => toast("Starting: the belt moves in about 3 seconds")).catch(() => {});
    },
  };

  // ---- incline bars
  function buildBars() {
    const bars = $("#bars");
    for (let i = 0; i <= 12; i++) {
      const b = document.createElement("button");
      b.className = "bar";
      b.dataset.pct = i;
      b.setAttribute("aria-label", `Set incline to ${i}%`);
      const fill = document.createElement("i");
      fill.style.height = `${18 + (i / 12) * 82}%`;
      b.appendChild(fill);
      b.addEventListener("pointerenter", () => { inclinePreview = i; render(); });
      b.addEventListener("click", () => { inclinePreview = null; setIncline(i); });
      bars.appendChild(b);
    }
    bars.addEventListener("pointerleave", () => { inclinePreview = null; render(); });
  }

  // ---- rendering
  const fmtTime = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  function render() {
    const now = Date.now();
    if (pending.speed && (now > pending.speed.until || Math.abs(S.speed_mph - pending.speed.v) < 0.06)) pending.speed = null;
    if (pending.incline && (now > pending.incline.until || Math.abs(S.incline_pct - pending.incline.v) < 0.05)) pending.incline = null;

    // status
    const st = $("#status");
    let text, cls;
    if (!S.connected) { text = `Not connected · ${(S.status || "searching for the pad…").replace(/^./, (c) => c.toLowerCase())}`; cls = "warn"; }
    else if (S.running) { text = S.speed_mph > 0.05 ? "Walking" : "Starting…"; cls = "live"; }
    else { text = "Connected · ready"; cls = "ready"; }
    st.className = `status ${cls}`;
    $("#status-text").textContent = text;
    $("#s-dot").className = `dot ${cls}`;
    $(".strip").classList.toggle("live", cls === "live");
    document.body.classList.toggle("offline", !S.connected);

    // speed
    const speed = S.connected ? S.speed_mph : 0;
    const big = $("#speed");
    if (speedPreview !== null) {
      big.textContent = speedPreview.toFixed(1);
      big.classList.add("preview");
      $("#speed-hint").textContent = S.running ? "Click to set" : "Press Start first";
    } else {
      big.textContent = S.connected ? speed.toFixed(1) : "–";
      big.classList.remove("preview");
      $("#speed-hint").textContent = !S.connected ? "Pad not connected"
        : pending.speed ? `Going to ${pending.speed.v.toFixed(1)}…` : "";
    }
    $("#g-val").setAttribute("d", speed >= MIN - 0.05 ? arc(A0, angleOf(speed)) : "");
    const maxA = angleOf(settings.max_speed_mph);
    $("#g-over").setAttribute("d", settings.max_speed_mph < MAX ? arc(maxA, A0 + SPAN) : "");
    const knob = $("#g-knob");
    const kv = speedPreview !== null ? speedPreview : pending.speed ? pending.speed.v : null;
    if (kv !== null) {
      const [x, y] = pt(angleOf(kv));
      knob.setAttribute("cx", x); knob.setAttribute("cy", y);
    }
    knob.classList.toggle("show", kv !== null);
    knob.classList.toggle("pending", speedPreview === null && kv !== null);
    $("#gauge").setAttribute("aria-valuenow", speed.toFixed(1));

    // incline
    const inc = S.connected ? Math.round(S.incline_pct) : 0;
    $("#incline").textContent = S.connected ? String(inc) : "–";
    $("#incline-hint").textContent = inclinePreview !== null && inclinePreview !== inc ? `→ ${inclinePreview}%`
      : pending.incline ? `→ ${pending.incline.v}%…` : "";
    $$(".bar").forEach((b) => {
      const i = +b.dataset.pct;
      b.classList.toggle("on", S.connected && i <= inc);
      b.classList.toggle("pre", inclinePreview !== null && i <= inclinePreview);
      b.classList.toggle("target", !!pending.incline && i === pending.incline.v);
    });

    // stats and footer
    $("#time").textContent = S.connected ? fmtTime(S.elapsed_s || 0) : "–";
    $("#dist").textContent = S.connected ? `${((S.distance_m || 0) / METERS_PER_MILE).toFixed(2)} mi` : "–";
    $("#kcal").textContent = S.connected ? String(S.kcal || 0) : "–";
    const n = S.workouts_waiting_for_health || 0;
    $("#health").textContent = n ? `${n} walk${n === 1 ? "" : "s"} waiting for Health` : "Apple Health up to date";
    $("#rec").classList.toggle("hidden", !S.recording_workout);

    renderGame();

    // strip
    $("#s-speed").textContent = S.connected ? speed.toFixed(1) : "–";
    $("#s-incline").textContent = S.connected ? String(inc) : "–";
    $("#s-time").textContent = S.connected ? fmtTime(S.elapsed_s || 0) : "–";

    if (desk && S.ask_close_at && S.ask_close_at > lastAskClose) {  // closed from the taskbar / Alt+F4 while walking
      lastAskClose = S.ask_close_at;
      askClose();
    }
    if (S.message && S.message_at > lastMsgAt) {
      lastMsgAt = S.message_at;
      toast(S.message);
    }
  }

  // ---- game mode
  let gamesShown = "";
  function renderGame() {
    const g = S.game;
    $("#btn-game").classList.toggle("hidden", !g);
    $("#s-game").classList.toggle("hidden", !g || !g.enabled);
    if (!g) return;
    const st = g.settings || {};
    const short = g.enabled ? g.short : "";
    const chip = $("#btn-game");
    chip.classList.toggle("on", g.enabled);
    chip.classList.toggle("paused", g.state === "paused");
    chip.setAttribute("aria-pressed", String(g.enabled));
    chip.title = `Game mode: ${g.label}${g.state === "paused" ? ` (${g.paused_s} s)` : ""}. Ctrl+Alt+G`;
    $("#game-text").textContent = short ? `Game · ${short}` : "Game";
    const badge = $("#s-game");
    badge.classList.toggle("hard", short === "hard");
    badge.classList.toggle("paused", g.state === "paused");
    $("#s-game-text").textContent = short || "game";

    // settings sheet
    $$("#gm-on button").forEach((b) => b.classList.toggle("sel", (b.dataset.on === "1") === g.enabled));
    $$("#gm-profile button").forEach((b) => b.classList.toggle("sel", b.dataset.profile === st.profile));
    let line = g.enabled ? g.label : "Off";
    if (g.enabled && g.state === "paused") line += ` (${g.paused_s} s)`;
    if (g.enabled && g.target_mph) line += ` · target ${g.target_mph.toFixed(1)} mph`;
    if (g.enabled && g.target_incline_pct !== null && g.target_incline_pct !== undefined) line += ` · ${g.target_incline_pct}%`;
    if (g.enabled) line += g.game_events ? " · deaths from Deadlock" : " · idle detection only";
    $("#gm-state").textContent = `Now: ${line}`;
    $("#gm-easy").textContent = st.easy_mph !== undefined ? st.easy_mph.toFixed(1) : "–";
    $("#gm-hard").textContent = st.hard_mph !== undefined ? st.hard_mph.toFixed(1) : "–";
    $("#gm-maxinc").textContent = st.max_incline_pct !== undefined ? String(st.max_incline_pct) : "–";
    $("#gm-climb-note").textContent = `Climbs 1% every ${st.climb_every_min} min in game, from ${st.start_incline_pct}%`;
    $$(".gm-speed").forEach((r) => r.classList.toggle("off", st.profile === "climb"));
    $$(".gm-climb").forEach((r) => r.classList.toggle("off", st.profile === "aim"));
    const games = st.games || [];
    if (games.join("|") !== gamesShown) {
      gamesShown = games.join("|");
      const box = $("#gm-games");
      box.innerHTML = "";
      games.forEach((name) => {
        const b = document.createElement("button");
        b.textContent = name;
        b.title = `Remove ${name}`;
        b.addEventListener("click", () => saveGame({ remove_game: name }));
        box.appendChild(b);
      });
      if (!games.length) box.textContent = "None yet";
    }
    const add = $("#gm-add");
    const canAdd = g.last_app && !games.includes(g.last_app);
    add.classList.toggle("hidden", !canAdd);
    if (canAdd) add.textContent = `Add ${g.last_app} (the last app you used)`;
  }
  async function saveGame(obj) {
    try { S.game = await api("POST", "/api/game", obj); } catch (e) { toast(e.message); }
    render();
  }

  // ---- polling
  async function poll() {
    try {
      S = await api("GET", "/api/status");
      failures = 0;
    } catch (e) {
      failures += 1;
      if (failures > 2) S = { ...S, connected: false, running: false, status: /token/i.test(e.message) ? "Open the link from TrekPad's settings" : "TrekPad isn't running on your PC" };
    }
    render();
    setTimeout(poll, failures > 2 ? 3000 : S.running ? 400 : 800);
  }

  // ---- settings sheet
  function renderCalories() {
    const src = settings.calorie_source || "weight";
    $$("#kcal-seg button").forEach((b) => b.classList.toggle("sel", b.dataset.src === src));
    $("#wt-val").textContent = settings.weight_lb ? String(settings.weight_lb) : "–";
    $("#weight-row").classList.toggle("off", src !== "weight");
  }
  async function loadSettings() {
    try { settings = await api("GET", "/api/settings"); } catch (_) { /* keep defaults */ }
    applyTheme();
    renderCalories();
    $("#max-val").textContent = settings.max_speed_mph.toFixed(1);
    $("#version").textContent = `TrekPad Control ${settings.version || ""}`;
    const hk = $("#hotkeys");
    hk.innerHTML = "";
    (settings.hotkeys || []).forEach(([keys, label]) => {
      const k = document.createElement("kbd"); k.textContent = keys;
      const s = document.createElement("span"); s.textContent = label;
      hk.append(k, s);
    });
  }
  async function saveSetting(obj) {
    try { settings = await api("POST", "/api/settings", obj); } catch (e) { toast(e.message); }
    applyTheme();
    renderCalories();
    $("#max-val").textContent = settings.max_speed_mph.toFixed(1);
    render();
  }
  async function copy(text, note) {
    try { await navigator.clipboard.writeText(text); toast(note); }
    catch (_) { window.prompt("Copy this:", text); }
  }

  // ---- wiring
  function wire() {
    buildGauge();
    buildBars();
    const hit = $("#g-hit");
    let dragging = false;
    hit.addEventListener("pointerdown", (e) => { dragging = true; hit.setPointerCapture(e.pointerId); speedPreview = limitSpeed(speedFromPointer(e)); render(); });
    hit.addEventListener("pointermove", (e) => { speedPreview = limitSpeed(speedFromPointer(e)); render(); });
    hit.addEventListener("pointerup", (e) => {
      if (!dragging) return;
      dragging = false;
      const v = speedFromPointer(e);
      speedPreview = null;
      setSpeed(v);
    });
    hit.addEventListener("pointercancel", () => { dragging = false; speedPreview = null; render(); });
    hit.addEventListener("pointerleave", () => { if (!dragging) { speedPreview = null; render(); } });

    $$("[data-act]").forEach((b) => b.addEventListener("click", () => ACTIONS[b.dataset.act]()));
    $("#btn-start").addEventListener("click", ACTIONS.start);
    $("#btn-stop").addEventListener("click", ACTIONS.stop);

    document.addEventListener("keydown", (e) => {
      if (e.target.closest && e.target.closest("input, textarea")) return;
      const map = { " ": "stop", Escape: "stop", ArrowUp: "speed-up", ArrowDown: "speed-down", ArrowRight: "incline-up", ArrowLeft: "incline-down" };
      if (e.key === "Escape" && !$("#settings").classList.contains("hidden")) { $("#settings").classList.add("hidden"); return; }
      if (map[e.key]) { e.preventDefault(); ACTIONS[map[e.key]](); }
    });

    $("#btn-settings").addEventListener("click", () => { loadSettings(); $("#settings").classList.remove("hidden"); });
    $$(".sheet").forEach((sh) => sh.addEventListener("click", (e) => {
      if (e.target === sh || e.target.closest("[data-close]")) sh.classList.add("hidden");
    }));
    $$("#theme-seg button").forEach((b) => b.addEventListener("click", () => saveSetting({ theme: b.dataset.theme })));
    const stepMax = (d) => saveSetting({ max_speed_mph: Math.round((settings.max_speed_mph + d) * 10) / 10 });
    $("#max-down").addEventListener("click", () => stepMax(-0.1));
    $("#max-up").addEventListener("click", () => stepMax(0.1));
    $("#btn-game").addEventListener("click", () => saveGame({ enabled: !(S.game && S.game.enabled) }));
    $$("#gm-on button").forEach((b) => b.addEventListener("click", () => saveGame({ enabled: b.dataset.on === "1" })));
    $$("#gm-profile button").forEach((b) => b.addEventListener("click", () => saveGame({ profile: b.dataset.profile })));
    $$("[data-gm]").forEach((b) => b.addEventListener("click", () => {
      const key = b.dataset.gm, st = (S.game && S.game.settings) || {};
      if (st[key] === undefined) return;
      const v = Math.round((st[key] + parseFloat(b.dataset.d)) * 10) / 10;
      saveGame({ [key]: v });
    }));
    $("#gm-add").addEventListener("click", () => S.game && S.game.last_app && saveGame({ add_game: S.game.last_app }));
    $$("#kcal-seg button").forEach((b) => b.addEventListener("click", () => saveSetting({ calorie_source: b.dataset.src })));
    const stepWt = (d) => saveSetting({ weight_lb: (settings.weight_lb || 187) + d, calorie_source: "weight" });
    $("#wt-down").addEventListener("click", () => stepWt(-1));
    $("#wt-up").addEventListener("click", () => stepWt(1));
    $("#copy-phone").addEventListener("click", () => copy(`${settings.lan_url}/#token=${token}`, "Phone link copied"));
    $("#copy-addr").addEventListener("click", () => copy(settings.lan_url, "Address copied"));
    $("#copy-token").addEventListener("click", () => copy(token, "Token copied"));

    // desktop window controls (only inside the TrekPad window, not on the phone)
    const onDesk = () => {
      if (desk) return;
      desk = window.pywebview.api;
      document.body.classList.remove("web");
      refit();
    };
    // Fit the window to its content (resize units differ slightly from CSS pixels at high
    // DPI, so measure, nudge, and measure again). Deliberately not driven by the resize
    // event: calling back into Python while the window is mid-resize risks a deadlock.
    const FULL_W = 400, MINI_W = 560, MINI_H = 52;
    let fitTries = 0, fitTimer;
    function fit() {
      if (!desk || fitTries >= 6) return;
      const mini = document.body.classList.contains("mini");
      const wantW = mini ? MINI_W : FULL_W;
      const wantH = mini ? MINI_H : $(".titlebar").offsetHeight + $("main.full").scrollHeight;
      const dw = wantW - innerWidth, dh = wantH - innerHeight;
      if (Math.abs(dw) > 1 || Math.abs(dh) > 1) {
        fitTries += 1;
        desk.grow(dw, dh, devicePixelRatio);
        fitTimer = setTimeout(fit, 350);
      }
    }
    function refit() { clearTimeout(fitTimer); fitTries = 0; fitTimer = setTimeout(fit, 400); }
    if (window.pywebview && window.pywebview.api) onDesk();
    window.addEventListener("pywebviewready", onDesk);
    $("#btn-min").addEventListener("click", () => desk && desk.minimize());
    $("#btn-mini").addEventListener("click", () => { if (desk) { document.body.classList.add("mini"); desk.set_mini(true, devicePixelRatio); refit(); } });
    $("#btn-expand").addEventListener("click", () => { document.body.classList.remove("mini"); if (desk) { desk.set_mini(false, devicePixelRatio); refit(); } });

    // Dragging the frameless window: the page tracks the pointer and asks Windows to move
    // the window (pywebview's built-in drag goes through the deadlock-prone path).
    $$(".drag").forEach((el) => {
      let start = null, raf = 0, last = null;
      el.addEventListener("pointerdown", (e) => {
        if (!desk || e.button !== 0) return;
        start = [e.screenX, e.screenY];
        el.setPointerCapture(e.pointerId);
        desk.drag_start();
      });
      el.addEventListener("pointermove", (e) => {
        if (!start) return;
        last = [e.screenX - start[0], e.screenY - start[1]];
        if (!raf) raf = requestAnimationFrame(() => { raf = 0; desk.drag_to(last[0], last[1], devicePixelRatio); });
      });
      const end = () => { start = null; };
      el.addEventListener("pointerup", end);
      el.addEventListener("pointercancel", end);
    });
    $("#btn-close").addEventListener("click", () => {
      if (!desk) return;
      if (S.connected && S.running) askClose(); else desk.close_app(false);
    });
    $("#close-stop").addEventListener("click", () => desk && desk.close_app(true));
    $("#close-keep").addEventListener("click", () => desk && desk.close_app(false));
  }
  function askClose() {
    if (document.body.classList.contains("mini")) {
      document.body.classList.remove("mini");
      if (desk) { desk.set_mini(false, devicePixelRatio); refit(); }
    }
    $("#confirm").classList.remove("hidden");
  }
  window.tp = { askClose };

  wire();
  loadSettings();
  render();
  poll();
})();
