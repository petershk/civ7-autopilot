"""Autonomous Civilization VII player.

Launches the game if needed, starts (or continues) a single-player game, then loops forever:
wait for our turn -> run a headless Claude Code session (with the civ7 MCP tools) to play the turn ->
heuristic fallback if anything is left blocking -> end turn. Periodic strategic reviews rewrite the
persistent strategy notes. Stops when the game is won/lost/over.

Usage:  python autopilot.py [--new] [--turn-model sonnet] [--review-model opus] [--max-turns N]
"""
import argparse
import datetime as dt
import glob
import json
import os
import socket
import subprocess
import sys
import time
import traceback

import brains
import control
import jev
from game import Game

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LOGS = os.path.join(HERE, "logs")
os.makedirs(STATE, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
STATUS = os.path.join(STATE, "status.json")
GAMEFILE = os.path.join(STATE, "game.json")
NOTES = os.path.join(STATE, "strategy_notes.md")
JOURNAL = os.path.join(STATE, "journal.md")
PRIMER = os.path.join(HERE, "strategy_primer.md")
HISTORY = os.path.join(STATE, "history.jsonl")
MCP_CFG = os.path.join(HERE, "mcp.json")
STEAM_APPID = "1295660"

DEFAULT_SETUP = {
    "Difficulty": "DIFFICULTY_PRINCE",
    "MapSize": "MAPSIZE_SMALL",
    "GameSpeeds": "GAMESPEED_STANDARD",
    "PlayerLeader": "AUTO",  # AUTO = the agent picks its leader
}


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOGS, "autopilot.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_status(**kw):
    cur = {}
    if os.path.exists(STATUS):
        try:
            cur = json.load(open(STATUS, encoding="utf-8"))
        except Exception:
            pass
    cur.update(kw)
    cur["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    json.dump(cur, open(STATUS, "w", encoding="utf-8"), indent=1)


# ---------------------------------------------------------------- game process
def port_open():
    try:
        socket.create_connection(("127.0.0.1", 4318), timeout=2).close()
        return True
    except OSError:
        return False


APPOPTIONS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Firaxis Games", "Sid Meier's Civilization VII", "AppOptions.txt")


def tuner_enabled():
    """True/False from AppOptions.txt, None if the game hasn't created the file yet (first run on this PC)."""
    try:
        txt = open(APPOPTIONS, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return None
    return "\nEnableTuner 1" in txt


def ensure_tuner_enabled():
    """Turn the FireTuner on in AppOptions.txt. The game only reads it at startup, and it rewrites the file
    itself, so only call this while the game is closed. Returns False if the file doesn't exist yet."""
    state = tuner_enabled()
    if state is None:
        log("AppOptions.txt not found yet (the game creates it on its first start)")
        return False
    if not state:
        import re
        txt = open(APPOPTIONS, encoding="utf-8", errors="replace").read()
        txt, n = re.subn(r"\n;?\s*EnableTuner\s+-?\d+", "\nEnableTuner 1", txt)
        if not n:  # the line is missing entirely: add it under [Debug]
            txt = txt.replace("[Debug]", "[Debug]\nEnableTuner 1", 1) if "[Debug]" in txt else txt + "\n[Debug]\nEnableTuner 1\n"
        open(APPOPTIONS, "w", encoding="utf-8").write(txt)
        log("enabled FireTuner in AppOptions.txt")
    return True


def game_running():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace").stdout
    return "civ7_" in out.lower()


def close_game():
    subprocess.run(["taskkill", "/F", "/IM", "Civ7_*"], capture_output=True)
    for _ in range(30):
        if not game_running():
            break
        time.sleep(1)
    time.sleep(3)  # let it finish writing its option files


def launch_game():
    # the game is open but was started without the tuner: it has to restart with the tuner on
    if game_running() and not port_open():
        log("Civ VII is running without the FireTuner port - closing it to restart with the tuner on")
        close_game()
    ensure_tuner_enabled()
    steam = r"C:\Program Files (x86)\Steam\steam.exe"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamExe")[0]
    except Exception:
        pass
    for attempt in range(3):
        log(f"launching Civ VII via {steam}")
        subprocess.Popen([steam, "-applaunch", STEAM_APPID])
        for i in range(300):
            if port_open():
                log("tuner port open")
                time.sleep(20)
                return
            # started but no port after 90s: the settings file had the tuner off (e.g. the game only just created
            # it on its first run). Fix it and restart the game.
            if i >= 45 and game_running() and tuner_enabled() is False:
                log("game started without the FireTuner - enabling it and restarting the game")
                close_game()
                ensure_tuner_enabled()
                break
            time.sleep(2)
        else:
            break
    raise RuntimeError(f"game did not open the FireTuner port (127.0.0.1:4318). Check that {APPOPTIONS} "
                       "has the line 'EnableTuner 1' and restart the game.")


# ---------------------------------------------------------------- shell / setup
def ui_state(g):
    try:
        r = g.raw("JSON.stringify({shell:UI.isInShell(),game:UI.isInGame(),load:UI.getGameLoadingState(),"
              "trans:(typeof Modding!=='undefined'&&Modding.getTransitionInProgress)?Modding.getTransitionInProgress():-1})")
    except Exception:
        return {}
    try:
        v = json.loads(r)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def enum_val(g, expr):
    try:
        return int(g.raw(expr))
    except Exception:
        return None


def start_new_game(g, setup, model="opus"):
    setup = dict(setup)
    if setup.get("PlayerLeader") == "AUTO":
        g.raw("Configuration.editGame().reset(GameModeTypes.SINGLEPLAYER); 'ok'")
        try:
            leaders = json.loads(g.raw("JSON.stringify((GameSetup.findPlayerParameter(GameContext.localPlayerID,'PlayerLeader')?.domain?.possibleValues||[])"
                                       ".filter(v=>v.value!='RANDOM' && v.invalidReason==GameSetupDomainValueInvalidReason.Valid).map(v=>v.value))"))
        except ValueError:
            leaders = []
        setup["PlayerLeader"] = pick_with_llm(
            "You are about to play a full game of Civilization VII (Antiquity to Modern, "
            f"{setup.get('Difficulty')}, {setup.get('MapSize')} map) completely autonomously via an API, aiming to win. "
            f"Choose the leader whose strengths are easiest to exploit well for an AI agent. Options: {leaders}. "
            "Answer with ONLY the exact leader key.", leaders, model) or "RANDOM"
    log(f"starting new game with {setup}")
    js = """(()=>{
      Configuration.editGame().reset(GameModeTypes.SINGLEPLAYER);
      try { GameSetup.loadCreateGameSettings && GameSetup.loadCreateGameSettings(); } catch(e) {}
      Configuration.editGame().reset(GameModeTypes.SINGLEPLAYER);
      const S = %s; const out = {};
      for (const k in S) {
        if (k === 'PlayerLeader' || k === 'PlayerCivilization') continue;
        try { GameSetup.setGameParameterValue(k, S[k]); out[k] = 'ok'; } catch(e) { out[k] = String(e); }
      }
      if (S.PlayerLeader) { try { GameSetup.setPlayerParameterValue(GameContext.localPlayerID, 'PlayerLeader', S.PlayerLeader); out.leader='ok'; } catch(e) { out.leader=String(e); } }
      if (S.PlayerCivilization) { try { GameSetup.setPlayerParameterValue(GameContext.localPlayerID, 'PlayerCivilization', S.PlayerCivilization); } catch(e) {} }
      return JSON.stringify(out);
    })()""" % json.dumps(setup)
    log("setup: " + g.raw(js))
    time.sleep(3)
    log("startGame: " + g.raw("engine.call('startGame'); 'sent'"))
    json.dump({"started": dt.datetime.now().isoformat(), "setup": setup, "status": "running"}, open(GAMEFILE, "w"), indent=1)
    for f in (NOTES, JOURNAL):
        if os.path.exists(f):
            os.replace(f, f + f".{int(time.time())}.bak")


def continue_latest_save(g):
    """Load the most recent single-player save (autosave or normal)."""
    log("loading most recent save")
    js = """(()=>{
      globalThis.__cbSaves = null;
      const h = engine.on('FileListQueryResults', (qid, list) => { globalThis.__cbSaves = list; try{GameStateStorage.closeFileListQuery(qid);}catch(e){} });
      GameStateStorage.querySaveGameList({ Location: SaveLocations.LOCAL_STORAGE, Type: SaveTypes.SINGLE_PLAYER,
        LocationOptions: SaveLocationCategories.AUTOSAVE | SaveLocationCategories.NORMAL | SaveLocationCategories.QUICKSAVE | SaveLocationOptions.LOAD_METADATA,
        ContentType: SaveFileTypes.GAME_STATE, ForceRefresh: true });
      return 'queried';
    })()"""
    g.raw(js)
    for _ in range(30):
        time.sleep(1)
        r = g.raw("globalThis.__cbSaves ? globalThis.__cbSaves.length : -1")
        if r not in ("-1", "undefined"):
            break
    js2 = """(()=>{
      const L = (globalThis.__cbSaves||[]).slice().sort((a,b)=>Number(b.saveTime)-Number(a.saveTime));
      if (!L.length) return 'none';
      const f = L[0];
      Configuration.editGame()?.reset(GameModeTypes.SINGLEPLAYER);
      Network.loadGame({ Location: f.location, LocationCategories: f.locationCategories, Type: f.type, ContentType: f.contentType,
        FileName: f.fileName, DisplayName: f.displayName, Slot: f.slot, AdditionalInfo: f.additionalInfo }, ServerType.SERVER_TYPE_NONE);
      return 'loading ' + f.fileName + ' turn ' + f.currentTurn;
    })()"""
    r = g.raw(js2)
    log(r)
    return r != "none"


def pass_loading_screen(g):
    """Wait through loading; press 'Begin Game' when it's waiting for the player."""
    presses = 0
    for _ in range(600):
        if not port_open():
            log("game process gone while loading")
            return False
        try:
            st = ui_state(g)
        except Exception:
            time.sleep(2)
            continue
        load = st.get("load")
        # UIGameLoadingState: 5 WaitingForVisualization, 6 WaitingForUIReady, 7 WaitingToStart, 8 GameStarted
        if load == 8 and st.get("game"):
            return True
        if load in (6, 7) or (load == 5 and presses == 0):
            if not st.get("shell"):
                try:
                    g.raw("UI.notifyUIReady(); 'ok'")
                    presses += 1
                    if presses <= 3 or presses % 10 == 0:
                        log(f"pressed Begin Game (loading state {load}, #{presses})")
                except Exception:
                    pass
            time.sleep(5)
            continue
        time.sleep(2)
    return False


def shell_age_transition(g, turn_model):
    """Single-player age transition that landed in the shell: choose the next civ and start."""
    opts = g.raw("""JSON.stringify((GameSetup.findPlayerParameter(GameContext.localPlayerID,'PlayerCivilization')?.domain?.possibleValues||[])
        .filter(v=>v.invalidReason==GameSetupDomainValueInvalidReason.Valid && v.value!='RANDOM').map(v=>v.value))""")
    try:
        options = json.loads(opts)
    except ValueError:
        options = []
    choice = pick_with_llm(f"Age transition in Civilization VII. Choose the civilization for the next age from: {options}. "
                           f"Consider our strategy notes:\n{read(NOTES)}\nAnswer with ONLY the exact key.", options, turn_model)
    log(f"shell age transition -> {choice}")
    if choice:
        g.raw(f"GameSetup.setPlayerParameterValue(GameContext.localPlayerID,'PlayerCivilization','{choice}'); 'ok'")
    time.sleep(2)
    g.raw("engine.call('startGame'); 'ok'")


def read(path, default=""):
    try:
        return open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return default


def pick_with_llm(prompt, options, model):
    if not options:
        return None
    spec = model if ":" in model else "claude:" + model
    out = brains.ask(spec, prompt) or ""
    words = out.strip().split()
    ans = words[-1].strip("`'\".*") if words else ""
    if ans in options:
        return ans
    for o in options:
        if o in out:
            return o
    log(f"pick_with_llm: no valid answer from {spec}; using first option")
    return options[0]


# ---------------------------------------------------------------- agent sessions
SYSTEM_PROMPT = """You are an expert, fully autonomous Civilization VII player. No human is watching or available:
never ask questions, never wait for confirmation — decide and act. You control the game only through the civ7 MCP tools.
Your goal is to WIN the game (legacy-path victory in the Modern age; otherwise the highest score), playing like a strong human:
expand early, keep every city productive, manage growth, research with purpose, use influence diplomatically, defend well,
and fight wars only when advantageous.

Rules of engagement for each turn:
1. You are given the briefing. Resolve EVERY pending decision thoughtfully (research, civic, production for each city with
   empty/finished queue, population placement, policies, celebrations, religion, narrative events, diplomacy responses,
   promotions, age-transition choices). Use the *_options tools to see choices before choosing.
2. Give every unit with moves a purposeful order: settlers to good sites (settle_spots) and found; military to defend
   threatened settlements, clear independents/barbarians when safe, escort settlers; scouts explore. auto_units is fine for
   routine units you have no specific plan for.
3. Check diplomacy: answer first meets and proposals; spend influence (befriend independents, endeavors) when useful.
4. Keep strategy notes current (write_notes) when the plan changes; log major decisions with log_journal (1 line).
5. Just before end_turn, call write_chronicle ONCE: 1-3 vivid sentences IN CHARACTER as our leader (first person,
   regal voice, no tool names/ids/coordinates) about this turn's events and your intentions. Add a short headline
   only for big moments (first contact, war/peace, city founded/captured, wonder, new age, legacy milestone).
6. Finish by calling end_turn. If it reports blockers, fix them (or call end_turn again to force the heuristic).
7. Learn as you play. Your instructions include skills and lessons from earlier games; follow them. Unsure what
   something does? lookup_rules (free). Discovered something non-obvious and reusable (a rule the game enforced,
   why an action failed and what worked instead, a strategy that paid off or backfired)? remember_lesson, so every
   future turn and game benefits. research_strategy (online, costs money, limited per age) only for important
   questions nothing else answers; then save the useful part with remember_lesson.
Be efficient: typically 8-30 tool calls per turn. Don't re-read state you already have. Coordinates are (x,y)."""


SKILLS = os.path.join(HERE, "skills")


def skills_text():
    """Game-knowledge skills (skills/<name>/SKILL.md) appended to every agent session's instructions.
    The agent runs with only the game tools, so skills are given to it directly rather than loaded on demand."""
    out = []
    for path in sorted(glob.glob(os.path.join(SKILLS, "*", "SKILL.md"))):
        body = read(path)
        if body.startswith("---"):  # drop the frontmatter; it's for Claude Code, not the game agent
            body = body.split("---", 2)[-1]
        out.append(body.strip())
    return "".join("\n\n" + b for b in out if b)


def run_claude(prompt, spec, tag, timeout=1500, max_rounds=None):
    """Run one agent session with the given brain spec ("claude:sonnet", "codex:gpt-5.5", "gemini:...", "ollama:...")."""
    if ":" not in spec and spec not in brains.OPENAI_COMPAT:
        spec = "claude:" + spec
    problem = brains.check(spec)
    if problem:  # e.g. switched on the dashboard to a brain this machine doesn't have
        log(f"{tag}: brain {spec} unavailable: {problem}")
        return f"brain unavailable: {problem}"
    logfile = os.path.join(LOGS, f"{tag}.jsonl")
    system = SYSTEM_PROMPT + "\n\n" + read(PRIMER) + skills_text()
    t0 = time.time()
    try:
        result, tools, cost, rc = brains.run_brain(spec, prompt, system, logfile, timeout, max_rounds)
    except Exception as e:
        result, tools, cost, rc = f"brain error: {e}", 0, None, "error"
    log(f"{tag}: rc={rc} tools={tools} {time.time()-t0:.0f}s cost={cost} brain={spec} :: {(result or '')[:300]!r}")
    return result


def get_brief(g):
    brief = {}
    for k, fn in (("overview", "overview"), ("pending", "pending"), ("cities", "cities"), ("units", "units"),
                  ("nearbyForeignUnits", "enemiesNear"), ("diplomacyPending", "diploPending"), ("standings", "rankings")):
        try:
            brief[k] = g.call(fn, 6) if fn == "enemiesNear" else g.call(fn)
        except Exception as e:
            brief[k] = f"error {e}"
    return brief


# Pending decisions the cheap model may handle; anything else (tech, civics, policies, government,
# religion, diplomacy, narrative events, age transition, unknown types) goes to the strong model.
EASY_PENDING = {"CHOOSE_CITY_PRODUCTION", "CHOOSE_TOWN_PROJECT", "NEW_POPULATION", "UNIT_PROMOTION_AVAILABLE",
                "ASSIGN_NEW_RESOURCES", "COMMAND_UNITS", "UNIT_PROMOTION",
                "DIPLOMATIC_ACTION_ESPIONAGE", "DIPLOMATIC_ACTION_AGENDA"}  # notices only; the clean-up step dismisses them


THREAT_RADIUS = 4  # hostile units this close to one of our cities make the turn "hard"


THREAT_IMMINENT = 2  # this close, every turn is hard
THREAT_RECHECK = 5   # a known, unchanged threat gets a fresh model look this often (turns)


def _city_dist(u, cities):
    """Tiles from this unit to our nearest city (99 if unknown)."""
    at = u.get("at") or [None, None]
    return min((max(abs(at[0] - c["at"][0]), abs(at[1] - c["at"][1]))
                for c in cities if at[0] is not None and c.get("at")), default=99)


def _near_city(u, cities):
    return _city_dist(u, cities) <= THREAT_RADIUS


# the threat the model last looked at: independents often loiter near a city for dozens of turns without
# attacking, so an unchanged threat shouldn't send every turn to the strong model
_threat_seen = {"turn": -999, "count": 0, "dist": 99}


def _threat_reason(near, cities, turn):
    hostile = [u for u in near if u.get("hostile") and _near_city(u, cities)]
    if not hostile:
        _threat_seen.update(turn=-999, count=0, dist=99)
        return ""
    count, dist = len(hostile), min(_city_dist(u, cities) for u in hostile)
    if dist <= THREAT_IMMINENT:
        why = f"hostile units {dist} tile(s) from a city"
    elif count > _threat_seen["count"] or dist < _threat_seen["dist"]:
        why = f"hostile units within {THREAT_RADIUS} tiles of a city (new or closer)"
    elif turn - _threat_seen["turn"] >= THREAT_RECHECK:
        why = f"hostile units still within {THREAT_RADIUS} tiles of a city (periodic re-check)"
    else:
        return ""  # same loitering units the model already handled
    _threat_seen.update(turn=turn, count=count, dist=dist)
    return why


def hard_turn_reasons(brief, review_this_turn, g=None):
    """Why this turn needs the strong model (empty list = routine turn). Rules, no model call."""
    why = []
    if review_this_turn:
        why.append("strategy review this turn")
    near = brief.get("nearbyForeignUnits")
    if not isinstance(near, list):
        why.append("no threat data")
    else:
        cities = brief.get("cities") if isinstance(brief.get("cities"), list) else []
        threat = _threat_reason(near, cities, (brief.get("overview") or {}).get("turn", 0)
                                if isinstance(brief.get("overview"), dict) else 0)
        if threat:
            why.append(threat)
    pend = brief.get("pending")
    if not isinstance(pend, dict):
        why.append("no pending data")
    else:
        hard = sorted({i.get("type", "?") for i in pend.get("items", []) if i.get("type") not in EASY_PENDING
                       and not str(i.get("type", "")).startswith("ADVISOR")})
        if hard:
            why.append("decisions: " + ",".join(hard))
        if pend.get("diplomacy"):
            why.append("diplomacy waiting")
    dp = brief.get("diplomacyPending")
    if not isinstance(dp, dict) or dp.get("statements") or dp.get("responses") or dp.get("callToArms"):
        why.append("diplomacy waiting")
    units = brief.get("units")
    idle_settlers = [u for u in units if u.get("canFound") and u.get("moves") and not u.get("headingTo")] if isinstance(units, list) else []
    # only worth a model if somewhere can actually be settled (with no site, the rules just hold the settler)
    if idle_settlers and (g is None or any(isinstance(s := g.call("settleSpots", u["id"], 1), list) and s for u in idle_settlers)):
        why.append("settler has a site to go to")
    return list(dict.fromkeys(why))


def turn_prompt(g, turn, brief=None):
    brief = brief or get_brief(g)
    notes = read(NOTES, "(none yet — write initial strategy notes this turn)")
    journal_tail = "\n".join(read(JOURNAL).splitlines()[-12:])
    return (f"It is turn {turn}. Play this turn completely, then call end_turn.\n\n"
            f"## Your strategy notes\n{notes}\n\n## Recent journal\n{journal_tail or '(empty)'}\n\n"
            f"## Briefing (fresh)\n```json\n{json.dumps(brief, separators=(',', ':'), ensure_ascii=False)}\n```")


def review_prompt(g, turn, reason):
    return (f"STRATEGIC REVIEW at turn {turn} ({reason}). Do NOT end the turn in this session and do not move units.\n"
            f"Study the empire and how we compare with rivals (get_briefing, get_rankings, get_players, research_options, civic_options, policies, diplomacy_status, "
            f"get_map around cities as needed). Then write_notes with a sharp plan: target victory/legacy paths this age, "
            f"expansion targets (coordinates), city build priorities, research & civic goals, military posture, diplomacy "
            f"stance per neighbour, and risks. Also adjust policies if clearly better. Log one journal line.\n"
            f"Then curate what we've learned: read_lessons, and if it has duplicates, contradictions or lessons this game "
            f"proved wrong, rewrite_lessons with a tidy version (under ~80 lines). If the biggest open strategic question "
            f"for this age isn't covered by the skills or lessons, you may use research_strategy once and save the answer "
            f"with remember_lesson.\n\n"
            f"## Current notes\n{read(NOTES, '(none)')}")


# ---------------------------------------------------------------- main loop
def wait_for_our_turn(g, turn_model, max_wait=1800):
    t0 = time.time()
    last = None
    while time.time() - t0 < max_wait:
        if not port_open():
            time.sleep(5)
            if not port_open():
                return "no-game"
        try:
            st = ui_state(g)
            if st.get("shell"):
                trans = st.get("trans")
                age = enum_val(g, "TransitionType.Age")
                if trans is not None and age is not None and trans == age:
                    shell_age_transition(g, turn_model)
                    pass_loading_screen(g)
                    continue
                return "shell"
            if "load" in st and not st.get("shell") and st.get("load") != 8:
                pass_loading_screen(g)
                continue
            if st.get("game"):
                try:
                    g.call("closePopups")
                except Exception:
                    pass
                ts = g.call("turnState")
                if not isinstance(ts, dict):
                    log(f"unexpected turnState reply {str(ts)[:80]!r}; resetting connection")
                    g.t.sock = None
                    time.sleep(2)
                    continue
                if ts.get("active") and not ts.get("sent"):
                    return ts
                # AI turns running; during diplomacy popups the engine can wait on us too
                pend = g.call("pending")
                if isinstance(pend, dict) and pend.get("diplomacy"):
                    g.call("autoResolve", False)
                if ts != last:
                    last = ts
        except (OSError, ConnectionError):
            if not port_open():
                return "no-game"
        except Exception as e:
            log(f"wait error: {e}")
        time.sleep(2)
    return "timeout"


def record_history(g, turn):
    """One line per turn: our stats and every known rival's, for the dashboard charts."""
    try:
        r = g.call("rankings")
        ov = g.call("overview")
        if not isinstance(r, dict):
            return
        rec = {"turn": turn, "age": ov.get("age") if isinstance(ov, dict) else None,
               "ageProgress": ov.get("ageProgress") if isinstance(ov, dict) else None,
               "players": [{k: p.get(k) for k in ("id", "name", "me", "sci", "cult", "gold", "happy", "infl", "settlements", "legacyTotal", "legacy")}
                           for p in r.get("players", [])]}
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"history record failed: {e}")


def spectator_pause(turn):
    """Honour the dashboard's pause button and turn delay."""
    shown = False
    while control.get().get("paused"):
        if not shown:
            log(f"paused by dashboard before T{turn}")
            write_status(state="paused", turn=turn)
            shown = True
        time.sleep(2)
    if shown:
        log("resumed")
    d = float(control.get().get("turnDelay") or 0)
    if d > 0:
        time.sleep(min(d, 300))


def game_over(g):
    try:
        s = g.call("gameStatus")
    except Exception:
        return None
    team = s.get("myTeam")
    for v in s.get("victories") or []:
        if v.get("place") == 1:
            return f"{'WON' if v.get('team') == team else 'LOST'}: {v.get('victory')} (team {v.get('team')})"
    if s.get("defeated"):
        return "DEFEATED"
    if s.get("ageOver") and s.get("finalAge"):
        return "FINAL AGE OVER"
    return None


def quiet_turn(g):
    """True when there is nothing to decide: no end-turn blocker (so no idle units or open choices),
    no diplomacy waiting and no hostile units near our units or cities."""
    try:
        ts = g.call("turnState")
        if ts.get("blocking") != "NONE":
            return False
        pend = g.call("pending")
        if not isinstance(pend, dict) or pend.get("items") or pend.get("diplomacy"):
            return False
        if (g.call("diploPending") or {}).get("responses"):
            return False
        near = g.call("enemiesNear", 6)
        return isinstance(near, list) and not any(u.get("hostile") for u in near)
    except Exception:
        return False


def fallback_finish_turn(g, turn):
    for attempt in range(4):
        ts = g.call("turnState")
        if ts.get("turn") != turn or not ts.get("active") or ts.get("sent"):
            return True
        res = g.call("autoResolve", True)
        if attempt:
            res = (res if isinstance(res, list) else [res]) + g.call("unstick")
        time.sleep(1)
        r = g.call("endTurn")
        log(f"fallback T{turn} attempt {attempt}: {res} -> {r}")
        if r.get("ok"):
            return True
        time.sleep(1.5)
    # still blocked: last resort, skip every unit and try again
    g.raw("(()=>{for(const u of Players.get(GameContext.localPlayerID).Units.getUnits()){try{Game.UnitOperations.sendRequest(u.id,'UNITOPERATION_SKIP_TURN',{X:-9999,Y:-9999,UnitAbilityType:-1})}catch(e){}}})()")
    time.sleep(1)
    r = g.call("endTurn")
    log(f"fallback T{turn} last resort -> {r}")
    return bool(r.get("ok"))


def single_instance():
    """Hold a localhost port for the process lifetime so a second autopilot exits immediately."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 8779))
    except OSError:
        log("another autopilot is already running - exiting")
        raise SystemExit(0)
    return s


def main():
    _lock = single_instance()  # noqa: F841 (kept alive for the process lifetime)
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", action="store_true", help="start a brand-new game (otherwise continue current/latest)")
    ap.add_argument("--turn-model", default="sonnet", help="alias: claude model for turns")
    ap.add_argument("--review-model", default="opus", help="alias: claude model for strategic reviews")
    ap.add_argument("--turn-brain", default="", help='e.g. "claude:sonnet", "codex:gpt-5.5", "gemini:gemini-2.5-pro", "ollama:qwen3.8", "openai:gpt-5.1"')
    ap.add_argument("--review-brain", default="", help="brain for strategic reviews (same format)")
    ap.add_argument("--review-every", type=int, default=10)
    ap.add_argument("--max-turns", type=int, default=100000)
    ap.add_argument("--routine-brain", default="jev",
                    help='who plays routine turns: "jev" (Jev picks, heuristics act), a brain spec like "claude:haiku", or "turn" (= the turn brain)')
    ap.add_argument("--retry-brain", default="", help="brain for the retry when a turn wasn't ended (default: the turn brain)")
    ap.add_argument("--model-every", type=int, default=3,
                    help="a model plays at least every N turns even when they're routine (0 = only when needed)")
    ap.add_argument("--max-rounds", type=int, default=30, help="cap on model rounds per turn session (0 = no cap)")
    ap.add_argument("--max-quiet-skips", type=int, default=2,
                    help="end up to this many consecutive turns with nothing to decide without a model session (0 = never)")
    ap.add_argument("--leader", default=DEFAULT_SETUP["PlayerLeader"])
    ap.add_argument("--difficulty", default=DEFAULT_SETUP["Difficulty"])
    ap.add_argument("--map-size", default=DEFAULT_SETUP["MapSize"])
    args = ap.parse_args()
    brains.ensure_mcp_config()
    default_turn = args.turn_brain or f"claude:{args.turn_model}"
    default_review = args.review_brain or f"claude:{args.review_model}"

    # which model does which job; the dashboard's Config tab overrides these defaults (state/control.json)
    def turn_brain():
        return control.get().get("turnBrain") or default_turn

    def review_brain():
        return control.get().get("reviewBrain") or default_review

    def routine_brain():
        v = control.get().get("routineBrain") or args.routine_brain
        return turn_brain() if v == "turn" else v

    def retry_brain():
        return control.get().get("retryBrain") or args.retry_brain or turn_brain()

    def model_every():
        v = control.get().get("modelEvery")
        return args.model_every if v is None or v == "" else int(v)

    # what "default" means for each dashboard setting (the command-line values)
    defaults = {"turnBrain": default_turn, "reviewBrain": default_review,
                "routineBrain": default_turn if args.routine_brain == "turn" else args.routine_brain,
                "retryBrain": args.retry_brain or "same as hard turns", "modelEvery": args.model_every}
    write_status(defaults=defaults)

    # make sure every chosen brain can actually run here before touching the game
    roles = {"turn": turn_brain(), "review": review_brain(), "routine": routine_brain(), "retry": retry_brain()}
    for role, spec in roles.items():
        problem = brains.check(spec)
        if problem:
            log(f"WARNING: {role} brain {spec} unavailable: {problem}")
    if brains.check(roles["turn"]):
        log(f"cannot play: the turn brain {roles['turn']} is unavailable. Choose another with --turn-brain "
            f"or on the dashboard. Usable here: {', '.join(b['spec'] for b in brains.available()) or 'none'}")
        sys.exit(1)

    if not port_open():
        launch_game()
    g = Game()
    setup = dict(DEFAULT_SETUP, PlayerLeader=args.leader, Difficulty=args.difficulty, MapSize=args.map_size)

    # get into a game
    for _ in range(3):
        st = ui_state(g)
        if st.get("shell"):
            time.sleep(10)  # let the main menu finish loading
            if args.new or not os.path.exists(GAMEFILE) or json.load(open(GAMEFILE)).get("status") != "running":
                start_new_game(g, setup, review_brain())
            elif not continue_latest_save(g):
                start_new_game(g, setup, review_brain())
            args.new = False
            time.sleep(10)
            pass_loading_screen(g)
        elif st.get("game"):
            if args.new:
                log("in a game but --new requested: exiting to main menu")
                g.raw("engine.call('exitToMainMenu'); 'ok'")
                time.sleep(25)
                continue
            if not os.path.exists(GAMEFILE):
                json.dump({"started": dt.datetime.now().isoformat(), "setup": "adopted existing game", "status": "running"}, open(GAMEFILE, "w"), indent=1)
            pass_loading_screen(g)
            break

    played = 0
    REVIEWF = os.path.join(STATE, "review.json")
    try:
        rv = json.load(open(REVIEWF))
        last_review_turn, last_age = rv.get("turn", -999), rv.get("age")
    except Exception:
        last_review_turn, last_age = -999, None
    quiet_streak = 0
    last_model_turn = -999
    while played < args.max_turns:
        if code_changed():  # between turns, so no session is cut off
            log("autopilot code changed on disk - restarting to load it")
            sys.exit(RESTART_CODE)
        st = wait_for_our_turn(g, turn_brain())
        if st == "no-game":
            log("game process gone - relaunching and continuing latest save")
            launch_game(); g = Game()
            if ui_state(g).get("shell"):
                time.sleep(10); continue_latest_save(g); time.sleep(10); pass_loading_screen(g)
            continue
        if st == "shell":
            over = game_over(g)
            log(f"back in main menu (game over? {over})")
            if json.load(open(GAMEFILE)).get("status") == "running" and not over:
                continue_latest_save(g); time.sleep(10); pass_loading_screen(g)
                continue
            break
        if st == "timeout":
            log("timed out waiting for our turn; poking")
            try:
                g.call("autoResolve", False)
            except Exception:
                pass
            continue

        over = game_over(g)
        if over:
            log(f"GAME OVER: {over}")
            write_status(state="finished", result=over)
            gf = json.load(open(GAMEFILE)); gf["status"] = "finished"; gf["result"] = over
            json.dump(gf, open(GAMEFILE, "w"), indent=1)
            # take the 'one more turn'-free path: exit
            break

        turn = st["turn"]
        record_history(g, turn)
        spectator_pause(turn)
        if control.get().get("followCamera"):
            try:
                g.call("focusCity", "")
            except Exception:
                pass
        try:
            ov = g.call("overview")
        except Exception:
            ov = {}
        age = ov.get("age")
        write_status(state="playing", turn=turn, age=age, overview=ov, turnBrain=turn_brain(), reviewBrain=review_brain(),
                     defaults=defaults)

        # strategic review
        if age != last_age or turn - last_review_turn >= args.review_every or not os.path.exists(NOTES):
            reason = "new age" if (last_age and age != last_age) else ("start" if last_age is None else "periodic")
            try:
                run_claude(review_prompt(g, turn, reason), review_brain(), f"review_T{turn:03d}", timeout=1200)
            except Exception as e:
                log(f"review failed: {e}")
            last_review_turn, last_age = turn, age
            json.dump({"turn": turn, "age": age}, open(REVIEWF, "w"))

        # nothing to decide: end the turn without paying for a model session (the agent still sees
        # every turn that has a decision, a threat or diplomacy, and at least every few turns regardless)
        if last_review_turn != turn and quiet_streak < args.max_quiet_skips and quiet_turn(g):
            r = g.call("endTurn")
            if r.get("ok"):
                # a unit finishing an automated order can un-ready the turn; then play it normally
                time.sleep(1.5)
                ts = g.call("turnState")
                r["ok"] = not (ts.get("turn") == turn and ts.get("active") and not ts.get("sent"))
            if r.get("ok"):
                quiet_streak += 1
                log(f"T{turn}: quiet turn (nothing pending, no threats nearby) - ended without a model session")
                played += 1
                continue
        quiet_streak = 0

        # play the turn: routine turns go to the routine brain (Jev by default); anything with a real
        # decision, plus a periodic check-in, goes to the turn brain
        rounds = args.max_rounds or None
        result = ""
        try:
            brief = get_brief(g)
            why = hard_turn_reasons(brief, last_review_turn == turn, g)
            every = model_every()
            if not why and every and turn - last_model_turn >= every:
                why = [f"model check-in (every {every} turns)"]
            brain = turn_brain() if why else routine_brain()
            log(f"T{turn}: {brain} ({'hard: ' + '; '.join(why) if why else 'routine turn'})")
            if brain.startswith("jev"):
                if play_jev_turn(g, turn, brief, brain):
                    played += 1
                    continue
                brain = turn_brain()
            last_model_turn = turn
            result = run_claude(turn_prompt(g, turn, brief), brain, f"turn_T{turn:03d}", max_rounds=rounds) or ""
        except (OSError, ConnectionError) as e:
            # game closed or crashed: the next wait sees "no-game" and relaunches + continues the save
            log(f"T{turn}: lost the game connection ({e})")
            g.t.sock = None
            continue
        except Exception as e:
            log(f"turn session failed: {e}\n{traceback.format_exc()}")

        try:
            finish_turn(g, turn, result, rounds, retry_brain)
        except (OSError, ConnectionError) as e:
            # game closed or crashed mid-turn: the next wait sees "no-game" and relaunches + continues the save
            log(f"T{turn}: lost the game connection after the session ({e})")
            g.t.sock = None
        played += 1

    log("autopilot finished")


def play_jev_turn(g, turn, brief, spec):
    """Routine turn without a model session: Jev picks each routine decision, heuristics move units.
    Returns True if the turn ended; False hands the turn to the turn brain."""
    if not jev.available():
        log(f"T{turn}: no DEFAPI_KEY for Jev; using the turn brain")
        return False
    t0 = time.time()
    try:
        res = jev.play_routine_turn(g, turn, brief, read(NOTES), spec, log)
    except (OSError, ConnectionError):
        raise
    except Exception as e:
        res = {"ended": False, "cost": 0.0, "decisions": [], "handoff": f"Jev error: {e}"}
    picks = "; ".join(f"{d['what']}: {d['pick']}" + (f" ({d['p']:.0%})" if isinstance(d.get("p"), (int, float)) else "")
                      + ("" if d.get("ok") else " [failed]") for d in res["decisions"]) or "no choices needed"
    tail = f" | handed to model: {res['handoff']}" if res.get("handoff") else ""
    # same shape as model-session lines so the dashboard's history and cost views pick it up
    log(f"jev_T{turn:03d}: rc={'ok' if res['ended'] else 'handoff'} tools={len(res['decisions'])} {time.time()-t0:.0f}s "
        f"cost={res['cost']:.6f} brain={spec} :: {picks}{tail}")
    with open(os.path.join(STATE, "jev_decisions.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"turn": turn, **{k: res.get(k) for k in ("ended", "cost", "decisions", "handoff", "errors")}},
                           ensure_ascii=False) + "\n")
    return bool(res["ended"])


def finish_turn(g, turn, result, rounds, turn_brain):
    """After the turn session: end the turn if the agent didn't, via retry then heuristic fallback."""
    ts = g.call("turnState")
    if ts.get("turn") == turn and ts.get("active") and not ts.get("sent") and ts.get("blocking") == "NONE":
        # the agent did its work but stopped before end_turn
        if g.call("endTurn").get("ok"):
            log(f"T{turn}: agent stopped before end_turn with nothing blocking; ended it")
        ts = g.call("turnState")
    if ts.get("turn") == turn and ts.get("active") and not ts.get("sent") and result.startswith(brains.CAPPED):
        # a capped session was going in circles; a retry would do the same
        log(f"T{turn}: session hit the {rounds}-round cap; using fallback")
        fallback_finish_turn(g, turn)
        ts = g.call("turnState")
    if ts.get("turn") == turn and ts.get("active") and not ts.get("sent"):
        # a session that bailed out early (model glitch) gets one fresh retry before the heuristic fallback
        log(f"T{turn}: agent did not end the turn; retrying once with a fresh session")
        try:
            run_claude(turn_prompt(g, turn) + "\n\n(Retry: the previous attempt stopped before ending the turn. "
                       "Finish the remaining decisions and call end_turn.)", turn_brain(), f"turn_T{turn:03d}_retry",
                       max_rounds=rounds and max(10, rounds // 2))
        except Exception as e:
            log(f"retry failed: {e}")
        ts = g.call("turnState")
    if ts.get("turn") == turn and ts.get("active") and not ts.get("sent"):
        log(f"T{turn}: agent did not end the turn; using fallback")
        fallback_finish_turn(g, turn)


RESTART_CODE = 3  # exit code that asks the supervisor for a fresh process
# modules the autopilot process loads (civ_mcp.py and bot*.js are reloaded on their own)
CODE_FILES = [os.path.join(HERE, f) for f in ("autopilot.py", "brains.py", "control.py", "jev.py",
                                              "learning.py", "game.py", "tuner.py")]
_code_stamp = {f: os.path.getmtime(f) for f in CODE_FILES if os.path.exists(f)}


def code_changed():
    return any(os.path.exists(f) and os.path.getmtime(f) != t for f, t in _code_stamp.items())


def supervise():
    """Run the autopilot in a child process and start a fresh one whenever it exits with RESTART_CODE
    (its code changed). --new only applies to the first run: restarts continue the game."""
    args = sys.argv[1:]
    while True:
        rc = subprocess.call([sys.executable, "-u", os.path.abspath(__file__)] + args,
                             env=dict(os.environ, CIV_AUTOPILOT_CHILD="1"))
        if rc != RESTART_CODE:
            sys.exit(rc)
        args = [a for a in args if a != "--new"]
        time.sleep(2)


if __name__ == "__main__":
    if os.environ.get("CIV_AUTOPILOT_CHILD"):
        main()
    else:
        supervise()
