# Civ VII autonomous player

An AI agent that plays a whole game of Sid Meier's Civilization VII on its own. It handles strategy,
every turn, city management, research, diplomacy and age transitions, and never asks a human anything.
It also learns: it saves lessons from its own games and can research strategy online.

## How it works
```
autopilot.py  ──(each turn)──>  claude -p  ──MCP tools──>  civ_mcp.py  ──>  game.py / tuner.py
   (supervisor)                   (Opus: strategy reviews,                   (FireTuner TCP :4318)
        │                          Sonnet: hard turns)                              │
        └──(routine turns)──> jev.py (Jev picks options, rules act) ──>   bot*.js injected into the
                                                                           game's "App UI" JS context
```
- **tuner.py**: a client for the game's FireTuner debug port (Tuner4 protocol). It evaluates JavaScript
  inside the running game. The JS source is percent-encoded because the tuner mangles backslashes.
- **bot.js / bot2.js / bot3.js / bot4.js**: the in-game helper library `CB`, built on the game's own UI APIs
  (`Game.UnitOperations`, `Game.CityOperations`, `Game.PlayerOperations`, and so on). It covers
  - units, cities, production, growth, tech and civics, policies, religion, narrative events,
    diplomacy (including calls to arms) and age transitions
  - `CB.autoResolve()` / `CB.unstick()`: heuristics that clear any end-turn blocker, so the game never stalls
  - `CB.closePopups()`: closes purely visual popups, cutscene placards and stale diplomacy screens
  - `CB.lookupRules()`: the game's own rules text (Civilopedia data)
- **game.py**: injects the JS and re-injects it automatically after the UI reloads (loading a save,
  changing age, going to the menu) or when a `bot*.js` file changes.
- **civ_mcp.py**: the MCP server that exposes about 50 game tools to the model.
- **autopilot.py**: the supervisor. It
  - launches Steam and the game if needed, then starts a new game (the agent picks its leader) or
    continues the latest save, and relaunches the game if it closes
  - clicks "Begin Game"
  - decides who plays each turn (see below), runs an Opus strategy review every 10 turns and at each new age
  - caps rounds per session, retries once, then uses the heuristic fallback if a turn isn't ended
  - stops when the game is won or lost
- **jev.py**: routine turns without a model session. [Jev](https://defapi.org) (a cheap decision API)
  chooses production, town focus and new-citizen tiles from the real options; the rules move units.
- **learning.py**: lessons the agent keeps across games, and optional online strategy research.
- **dashboard.py**: a live web dashboard at http://localhost:8777.

## Who plays what
Each turn is routed by rules (no model call):
- **Quiet turn** (nothing pending, no threats): ends with no model at all (at most 2 in a row).
- **Routine turn** (only production, growth, promotions): the routine brain, Jev by default.
- **Hard turn** (enemies within 4 tiles of a city, diplomacy, tech/civic/policy choices, a settler with
  somewhere to go, events): the turn brain, Claude Sonnet by default.
- A model also plays at least every 3 turns, and the strategist (Claude Opus) reviews every 10 turns.

Change any of these on the dashboard's **Settings** tab (they take effect next turn; spending is on the **Costs** tab), or with the
command-line defaults below. Other brains are supported too: Codex, Gemini, Ollama, OpenAI, OpenRouter.

## Learning and skills
- `skills/<name>/SKILL.md` files are added to the agent's instructions every session. `skills/settling`
  covers site selection, compiled from Civ VII guides.
- The agent saves reusable lessons with `remember_lesson` to `skills/learned/SKILL.md`; the strategist
  curates them at each review.
- `lookup_rules` searches the game's own rules text; `research_strategy` runs a short web-research session
  (limited per age; set on the dashboard, or turn learning off).

## Run
- `run_autopilot.bat` continues the current or latest game. Add `--new` to start a fresh one.
- `stop_autopilot.bat` stops the agent. The game itself keeps running.
- Editing the autopilot's code while it runs is fine: it restarts itself between turns to load the change.
  Model choices changed on the dashboard apply from the next turn, with no restart.
- Options: `--turn-model sonnet --review-model opus --routine-brain jev --model-every 3 --max-rounds 30
  --review-every 10 --difficulty DIFFICULTY_KING --map-size MAPSIZE_STANDARD --leader LEADER_X`

Requirements:
- Windows, Civilization VII on Steam, with `EnableTuner 1` in
  `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VII\AppOptions.txt` (the autopilot sets this itself).
- Python with `pip install mcp` (or `pip install -r requirements.txt` for everything). Missing packages can also
  be installed from the dashboard: **Settings → Setup** has an Install button for each one.
- The `claude` CLI, logged in.
- Optional: `DEFAPI_KEY` (defapi.org) for Jev routine turns. Without it, routine turns use the turn brain.

## Watch it
- The dashboard: chronicle, current session, standings, cities, map, charts, and cost per turn by role.
- `state/strategy_notes.md`: the agent's current plan. `state/journal.md`: one line per key decision.
- `logs/autopilot.log`: one line per session (time, tool calls, cost, who played, summary).
- `python view_log.py logs/turn_T042.jsonl`: the full reasoning and tool calls for a turn.
- `python game.py overview` (or `cities`, `units`, `pending`, `players` ...): query the live game.

Avoid clicking in the game window while it runs.
