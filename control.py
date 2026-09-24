"""Spectator controls shared by the dashboard, MCP server and autopilot (state/control.json)."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "control.json")
DEFAULTS = {
    "paused": False,        # autopilot waits before starting the next turn
    "followCamera": True,   # pan the game camera to whatever the agent acts on
    "actionDelay": 1.0,     # seconds to linger after each on-map action (so it's watchable)
    "turnDelay": 0,         # extra seconds between turns
    "chronicle": True,      # agent writes an in-character chronicle entry each turn
    "turnBrain": "",        # "" = use the command-line default, else e.g. "codex:gpt-5.5", "gemini:gemini-2.5-pro"
    "reviewBrain": "",
    "routineBrain": "",     # "" = command-line default (jev); "jev", a brain spec, or "turn" (= turnBrain)
    "retryBrain": "",       # "" = turnBrain
    "modelEvery": "",       # "" = command-line default (3): a model plays at least every N turns
    "learning": "online",   # off | ingame (rules lookup + lessons) | online (also web research)
    "researchPerAge": 3,    # max online research sessions per age
}


def get():
    try:
        with open(PATH, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def update(**kw):
    cur = get()
    cur.update({k: v for k, v in kw.items() if k in DEFAULTS})
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1)
    return cur
