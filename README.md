# Civ VII autonomous player

An AI agent that plays a whole game of Sid Meier's Civilization VII on its own. It handles strategy,
every turn, city management, research, diplomacy and age transitions, and never asks a human anything.
It also learns: it saves lessons from its own games and can research strategy online.

## How it works
```
autopilot.py  ──(each turn)──>  claude -p  ──MCP tools──>  civ_mcp.py  ──>  game.py / tuner.py
   (supervisor)                   (strategy reviews, lesson                  (FireTuner TCP :4318)
        │                          fact-checks, hard turns)                         │
        └──(other turns)──> jev.py (Jev picks options, rules act) ──>     bot*.js injected into the
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
  - decides who plays each turn (see below), runs a strategy review every 10 turns and at each new age,
    then a fact-check of new lessons
  - checks that every chosen brain is usable on this machine, at startup and before each session
  - caps rounds per session, retries once, then uses the heuristic fallback if a turn isn't ended
  - restarts itself between turns when its code changes; if an edit doesn't compile it waits for a fix, and it
    restarts after a crash (up to 5 times in 10 minutes)
  - stops when the game is won or lost
- **jev.py**: turns without a model session. [Jev](https://defapi.org) (a cheap decision API) gets each
  decision as a multiple-choice question (the real options plus the situation and the plan) and picks one;
  the rules handle whatever is left, so the turn always ends.
- **learning.py**: lessons the agent keeps across games, their audit trail, and optional online research.
- **dashboard.py**: a live web dashboard at http://localhost:8777.

## Who plays what
Each turn is routed by rules (no model call). There are two modes.

**Jev plays everything** (a switch on the Settings tab; needs a Jev key). Every decision with options goes to
Jev: production, growth, town focus, tech, civics, government, celebrations, religion, attributes, narrative
events, city-state bonuses, the next civilization, policies, first meetings, diplomatic responses, deals, calls
to arms, where each settler goes, and what each combat unit near enemies does (attack a specific unit, hold, or
fall back). The heuristics do the rest. Only emergencies go to the turn brain (if "hand emergencies to the
hard-turn model" is on):
- a settlement under attack: 2+ hostile military units next to it, or 3+ within 2 tiles (scouts and
  civilians don't count)
- a settlement with no defender of ours on or next to it, with a hostile military unit adjacent
- a turn Jev couldn't finish

A typical turn costs about $0.0002 this way.

**Normal mode**:
- **Quiet turn** (nothing pending, no threats): ends with no model at all (at most 2 in a row).
- **Routine turn** (only production, growth, promotions): the routine brain, Jev by default.
- **Hard turn** (tech/civic/policy choices, diplomacy, events, a settler with somewhere to go, or a threat):
  the turn brain, Claude Sonnet by default. A threat counts when hostile units are 2 tiles or less from a
  city, when a new one appears or one gets closer, and every 5 turns while the same one stays within 4 tiles,
  so units that just loiter don't send every turn to the model.
- A model also plays at least every few turns (model check-in).

In both modes the strategist reviews every 10 turns (configurable, or only at new ages). Change any of these on the dashboard's **Settings** tab (they take effect next turn; spending is on the **Costs** tab), or with the
command-line defaults below. Other brains are supported too: Codex, Gemini, Ollama, OpenAI, OpenRouter.

## Learning and skills
- `skills/<name>/SKILL.md` files are added to the agent's instructions every session. `skills/settling`
  covers site selection, compiled from Civ VII guides.
- The agent saves reusable lessons with `remember_lesson` to `skills/learned/SKILL.md`; the strategist
  curates them at each review.

### Auditing lessons and reasoning
Lessons go into every future session, so a wrong one quietly misleads every later decision. So:
- Every lesson carries a status: **[unverified]** (new: one observation or a guess), **[strategy]** (a
  judgment call data can't settle) or **[verified]** (checked against the game's data, or confirmed by you).
  Sessions are told to treat unverified lessons as hints.
- A rewrite can merge and reword lessons but can't mark anything as checked: a reworded lesson goes back to
  unverified.
- After each strategy review, a **fact-check session** tests the unverified lessons against the game's own
  rules data (`lookup_rules`, read-only game tools) and records a verdict and its evidence (`audit_lesson`).
  Refuted lessons are removed.
- Every change is logged in `state/lessons_ledger.jsonl` (who, which session, turn, evidence), the file is
  backed up to `state/lessons_history/` before each rewrite, and every strategy plan is kept in
  `state/notes_history/`.
- The dashboard's **Settings → What it knows** shows each lesson with its status and source, with
  **confirm / keep as strategy / reject** buttons, the change history, and the strategy plan over time.
- The fact-check trusts the game's data, so if a data field is itself misread, a wrong lesson can pass.
  Double-check lessons that rest on a single number.
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
- Optional: `DEFAPI_KEY` (defapi.org) for Jev: `setx DEFAPI_KEY "your-key"`. The autopilot picks it up
  without a restart. Without it, Jev's turns go to the turn brain.

On a new PC, the autopilot handles the first run itself: if the game creates its settings file with the tuner
off, it closes the game, turns the tuner on and relaunches. It also clears a leftover crash reporter window,
which otherwise stops Steam from starting the game.

## Watch it
- The dashboard: chronicle, current session, standings, cities, map, charts, and cost per turn by role.
- `state/strategy_notes.md`: the agent's current plan. `state/journal.md`: one line per key decision.
- `logs/autopilot.log`: one line per session (time, tool calls, cost, who played, summary).
- `python view_log.py logs/turn_T042.jsonl`: the full reasoning and tool calls for a turn.
- `state/jev_decisions.jsonl`: every Jev decision (the pick and its confidence) and what the heuristics did.
- `python game.py overview` (or `cities`, `units`, `pending`, `players` ...): query the live game.

## What steers the agent
Everything a game session is told, in order:
1. `SYSTEM_PROMPT` in `autopilot.py`: the goal (win) and the rules for each turn.
2. `strategy_primer.md`: how Civ VII works and a generic plan (hand-written).
3. `skills/*/SKILL.md`: game-knowledge skills, including the learned lessons.
4. The session's prompt: for a turn, the strategy plan, the last 12 journal lines and a fresh briefing.
5. The tool descriptions in `civ_mcp.py` (several carry rules, e.g. what's worth saving as a lesson).

Jev gets one question per decision plus a summary of the situation and the plan (`jev.py`). The heuristic
fallbacks (`bot3.js`) have fixed defaults, e.g. friendly first meetings, reject incoming deals, join calls to
arms. Game sessions can only use the game tools (no shell, files or web), and they don't load your own Claude
Code settings, CLAUDE.md or memory.

Avoid clicking in the game window while it runs.
