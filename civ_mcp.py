"""MCP server exposing Civilization VII to an LLM agent via the FireTuner bridge.

Run by `claude -p --mcp-config ...` from autopilot.py. All tools return compact JSON text.
"""
import json
import os
import time

from mcp.server.mcpserver import MCPServer

import control
import learning
from game import Game

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
NOTES = os.path.join(STATE_DIR, "strategy_notes.md")
JOURNAL = os.path.join(STATE_DIR, "journal.md")
CHRONICLE = os.path.join(STATE_DIR, "chronicle.jsonl")
os.makedirs(STATE_DIR, exist_ok=True)

app = MCPServer("civ7")
g = Game()


def J(x):
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False)


def call(fn, *a):
    return J(g.call(fn, *a))


def watch(kind, target):
    """Spectator mode: pan the game camera to what we're acting on, then linger briefly."""
    c = control.get()
    if not c.get("followCamera"):
        return
    try:
        if kind == "unit":
            g.call("focusUnit", target)
        elif kind == "city":
            g.call("focusCity", target)
        elif kind == "plot":
            g.call("focusPlot", target[0], target[1])
    except Exception:
        return
    time.sleep(max(0.0, min(float(c.get("actionDelay", 1.0)), 10.0)))


# ---------------- state ----------------
@app.tool()
def get_briefing() -> str:
    """Full situation report for the current turn: empire overview, pending decisions/blockers,
    cities, units (ids, positions, moves), visible nearby foreign units, diplomacy items, recent events.
    Call this first every turn."""
    try:
        g.call("closePopups")
    except Exception:
        pass
    out = {
        "overview": g.call("overview"),
        "pending": g.call("pending"),
        "cities": g.call("cities"),
        "units": g.call("units"),
        "nearbyForeignUnits": g.call("enemiesNear", 6),
        "diplomacyPending": g.call("diploPending"),
        "standings": g.call("rankings"),
        "recentEvents": g.call("recentLog") if False else None,
    }
    try:
        log = json.loads(g.raw("JSON.stringify((CB.log||[]).filter(e=>e.turn>=Game.turn-1).slice(-15))"))
        out["recentEvents"] = log
    except Exception:
        pass
    return J(out)


@app.tool()
def get_map(x: int, y: int, radius: int = 5) -> str:
    """ASCII map of revealed tiles centred on (x,y). Rows are y, columns x from origin. Legend included.
    Note: Civ VII uses offset hex coordinates; odd/even rows are shifted."""
    return call("mapAround", x, y, min(radius, 9))


@app.tool()
def get_plot(x: int, y: int) -> str:
    """Details of one tile: terrain, feature, resource, river, owner, units."""
    return call("plotInfo", x, y)


@app.tool()
def get_rankings() -> str:
    """How we compare with known rival civs (as shown in the game's diplomacy ribbon and Age Rankings):
    yields, settlements, legacy-path scores vs the final milestone, and our rank (1 = best) on each."""
    return call("rankings")


@app.tool()
def get_players() -> str:
    """Met civilizations, city-states and independents: relationship, war/alliance status, cities, suzerains."""
    return call("players")


# ---------------- units ----------------
@app.tool()
def unit_actions(unit_id: str) -> str:
    """List the operations/commands this unit can currently perform (e.g. op:FOUND_CITY, op:FORTIFY, cmd:UPGRADE).
    '(targets:N)' means the action needs a target tile x,y."""
    return call("unitActions", unit_id)


@app.tool()
def unit_move(unit_id: str, x: int, y: int) -> str:
    """Move a unit toward (x,y) (multi-turn paths are queued). Moving onto an enemy performs a melee attack;
    ranged units targeting an enemy in range perform a ranged attack. Will refuse if it would start a war."""
    watch("unit", unit_id)
    r = call("moveTo", unit_id, x, y)
    watch("plot", (x, y))
    return r


@app.tool()
def unit_action(unit_id: str, action: str, x: int = -1, y: int = -1) -> str:
    """Perform a unit action by name, e.g. FOUND_CITY, FORTIFY, SLEEP, SKIP_TURN, REST_UNTIL_HEALED, AUTOMATE_EXPLORE,
    RANGE_ATTACK (needs x,y), PILLAGE, UNITCOMMAND_UPGRADE, UNITCOMMAND_PACK_ARMY, UNITCOMMAND_UNPACK_ARMY (x,y),
    UNITCOMMAND_CONSTRUCT, UNITCOMMAND_MAKE_TRADE_ROUTE, UNITCOMMAND_DELETE ... Use x=-1,y=-1 when no target.
    On failure returns reasons and valid target tiles."""
    watch("unit", unit_id)
    if x == -1 or y == -1:
        return call("unitDo", unit_id, action)
    r = call("unitDo", unit_id, action, x, y)
    watch("plot", (x, y))
    return r


@app.tool()
def settle_spots(unit_id: str = "") -> str:
    """Game-AI recommended city sites near the given settler (or capital), with pros/cons."""
    return call("settleSpots", unit_id or None, 5)


@app.tool()
def promote_unit(unit_id: str, promotion: str = "") -> str:
    """Promote a unit/commander. With empty promotion, lists options instead."""
    if not promotion:
        return call("promotionOptions", unit_id)
    return call("promote", unit_id, promotion)


@app.tool()
def auto_units() -> str:
    """Let the heuristic handle all idle units this turn (settlers go to best spot/found, scouts explore,
    ranged units shoot hostiles in range, others fortify). Use for units you have no specific plan for."""
    return call("autoUnits")


# ---------------- cities ----------------
@app.tool()
def city_build_options(city: str, purchase: bool = False) -> str:
    """What a city (id or name) can produce (units, buildings, projects) with turns; purchase=True shows gold prices."""
    return call("buildOptions", city, purchase)


@app.tool()
def city_build(city: str, item: str, purchase: bool = False, x: int = -1, y: int = -1) -> str:
    """Queue (or buy with gold if purchase=True) a unit/building/project in a city. item = key (UNIT_SETTLER,
    BUILDING_GRANARY) or name. Buildings are auto-placed on the best tile unless x,y given.
    Tip: production replaces nothing - it appends; use city_clear_queue first to switch."""
    watch("city", city)
    if x == -1 or y == -1:
        return call("build", city, item, purchase)
    return call("build", city, item, purchase, x, y)


@app.tool()
def city_clear_queue(city: str) -> str:
    """Remove everything from a city's production queue (to switch production)."""
    return call("clearQueue", city)


@app.tool()
def city_growth_options(city: str = "") -> str:
    """When a settlement grew (NEW_POPULATION), list tiles it can expand onto (with yields) and specialist slots."""
    return call("growthOptions", city or None)


@app.tool()
def city_place_population(city: str, x: int, y: int, specialist: bool = False) -> str:
    """Resolve population growth: expand onto tile (x,y), or add a specialist on urban tile (x,y) if specialist=True."""
    watch("plot", (x, y))
    return call("placePop", city or None, x, y, specialist)


@app.tool()
def town_focus(city: str, focus: str = "") -> str:
    """Towns: with empty focus lists focus options; otherwise set focus (GROWTH or a project key/name like farming/fishing/trade)."""
    if not focus:
        return call("townFocusOptions", city)
    return call("setTownFocus", city, focus)


@app.tool()
def upgrade_town(city: str) -> str:
    """Spend gold to convert a town into a city."""
    return call("upgradeTown", city)


# ---------------- progression ----------------
@app.tool()
def research_options() -> str:
    """Available technologies with turns and what they unlock."""
    return call("techOptions")


@app.tool()
def set_research(tech: str) -> str:
    """Choose the technology to research (key or name)."""
    return call("setTech", tech)


@app.tool()
def civic_options() -> str:
    """Available civics with turns and unlocks."""
    return call("civicOptions")


@app.tool()
def set_civic(civic: str) -> str:
    """Choose the civic to study (key or name)."""
    return call("setCivic", civic)


@app.tool()
def policies() -> str:
    """Policy/tradition slots, active cards and available cards."""
    return call("policies")


@app.tool()
def set_policies(activate: list[str] = [], deactivate: list[str] = []) -> str:
    """Change policy cards: deactivate first, then activate. Names or keys. Call with both lists empty to keep
    the current cards (this acknowledges a TRADITIONS_AVAILABLE prompt)."""
    res = []
    for d in deactivate:
        res.append(g.call("setPolicy", d, False)); time.sleep(0.4)
    for a in activate:
        res.append(g.call("setPolicy", a, True)); time.sleep(0.4)
    g.call("policiesDone")
    return J(res)


CHOICES = {
    "government": ("governmentOptions", "setGovernment"),
    "celebration": ("celebrationOptions", "chooseCelebration"),
    "pantheon": ("pantheonOptions", "choosePantheon"),
    "religion": ("religionOptions", "foundReligion"),
    "belief": ("beliefOptions", "addBelief"),
    "attribute": ("attributeOptions", "buyAttribute"),
    "narrative": ("narrative", "chooseNarrative"),
    "city_state_bonus": ("cityStateBonusOptions", "chooseCityStateBonus"),
    "next_civilization": ("nextCivOptions", "chooseNextCiv"),
    "legacy_cards": ("legacyCards", "legacyAdd"),
}


@app.tool()
def choice_options(kind: str) -> str:
    """Options for a pending decision. kind: government, celebration, pantheon, religion, belief, attribute,
    narrative, city_state_bonus, next_civilization (age transition civ), legacy_cards."""
    if kind not in CHOICES:
        return J({"error": f"kind must be one of {list(CHOICES)}"})
    return call(CHOICES[kind][0])


@app.tool()
def make_choice(kind: str, option: str) -> str:
    """Resolve a pending decision (see choice_options). option = key or name (narrative: option key or index;
    legacy_cards: card id - call finish_legacies when done)."""
    if kind not in CHOICES:
        return J({"error": f"kind must be one of {list(CHOICES)}"})
    if kind == "narrative" and option.isdigit():
        return call("chooseNarrative", int(option))
    return call(CHOICES[kind][1], option)


@app.tool()
def finish_legacies(auto_fill: bool = True) -> str:
    """Finish age-start legacy/dedication selection (auto_fill adds any remaining affordable cards)."""
    return call("autoLegacies" if auto_fill else "legacyFinish")


# ---------------- diplomacy ----------------
@app.tool()
def diplomacy_status() -> str:
    """Pending diplomatic statements (first meets, deal proposals), actions awaiting your response,
    ongoing diplomatic actions/wars, and met players."""
    return J({"pending": g.call("diploPending"), "ongoing": g.call("ongoingActions"), "players": g.call("players")})


@app.tool()
def respond_first_meet(session_id: int, player_id: int, attitude: str = "FRIENDLY") -> str:
    """Answer a first-meeting greeting: FRIENDLY, NEUTRAL or UNFRIENDLY."""
    return call("respondFirstMeet", session_id, player_id, attitude)


@app.tool()
def respond_diplomatic_action(action_id: int, response: str) -> str:
    """Respond to an AI diplomatic action awaiting your answer (e.g. 'support', 'reject', or option index)."""
    r = int(response) if response.isdigit() else response
    return call("respondAction", action_id, r)


@app.tool()
def answer_deal(player_id: int, accept: bool) -> str:
    """Accept or reject a deal/peace proposal from a player."""
    return call("answerDeal", player_id, accept)


@app.tool()
def dismiss_diplomacy_session(session_id: int) -> str:
    """Close an informational diplomacy dialog (e.g. a war declaration notice)."""
    return call("closeSession", session_id)


@app.tool()
def diplomatic_action_options(target_player: int) -> str:
    """Diplomatic actions (endeavors, sanctions, treaties, espionage, befriend independent, suzerain perks)
    currently available against/with a player, with influence costs."""
    return call("diploActions", target_player)


@app.tool()
def start_diplomatic_action(target_player: int, action: str, second_param: str = "", second_id: int = -1) -> str:
    """Start a diplomatic action (key from diplomatic_action_options). Some need a second target (param + id)."""
    if second_param:
        return call("startDiploAction", target_player, action, second_param, second_id)
    return call("startDiploAction", target_player, action)


@app.tool()
def support_diplomatic_action(action_id: int, support_initiator: bool = True) -> str:
    """Spend influence to support an ongoing diplomatic action (or its target side)."""
    return call("supportAction", action_id, support_initiator)


@app.tool()
def declare_war(target_player: int, formal: bool = True) -> str:
    """Declare war (formal if possible, otherwise surprise war). Serious decision - check military strength first."""
    return call("declareWar", target_player, formal)


@app.tool()
def answer_call_to_arms(accept: bool) -> str:
    """Answer an ally's call to arms (shown as diplomacyPending.callToArms / pending DIPLOMATIC_ALLY_AT_WAR).
    accept=True: we declare war on the ally's enemy. accept=False: we decline, which ENDS our alliance with them.
    It blocks the turn until answered."""
    return call("answerCallToArms", accept)


@app.tool()
def propose_peace(target_player: int) -> str:
    """Propose a white peace to a player you are at war with."""
    return call("proposePeace", target_player)


@app.tool()
def form_alliance(target_player: int) -> str:
    """Propose/form an alliance with a major civ."""
    return call("formAlliance", target_player)


# ---------------- turn flow ----------------
@app.tool()
def auto_resolve(include_units: bool = False) -> str:
    """Resolve any remaining blocking decisions with simple heuristics (and idle units if include_units).
    Use only for things you don't care about - your own choices are better."""
    return call("autoResolve", include_units)


@app.tool()
def end_turn() -> str:
    """End the turn. If something still blocks, returns the blockers; call end_turn again to let the
    heuristic resolve leftovers (second consecutive call forces it)."""
    try:
        g.call("closePopups")
    except Exception:
        pass
    r = g.call("endTurn")
    ack = {"TRADITIONS": "policiesDone"}  # "consider" prompts: ending the turn means we keep the current setup
    if not r.get("ok") and r.get("blocking") in ack:
        g.call(ack[r["blocking"]])
        time.sleep(0.8)
        r = g.call("endTurn")
        r["note"] = "kept current policies (acknowledged new policy cards)"
    # advisor warnings are information, never decisions; they arrive in chains (the next shows up a moment
    # after the previous is dismissed), so keep marking them viewed while they are the blocker
    for _ in range(5):
        if r.get("ok") or r.get("blocking") != "VIEW_ADVISOR_WARNING":
            break
        g.call("dismissAdvisor")
        time.sleep(0.8)
        r = g.call("endTurn")
        r["note"] = "dismissed advisor warnings"
    if r.get("ok"):
        # verify it stuck: a unit finishing an automated order can un-ready the turn
        time.sleep(1.2)
        ts = g.call("turnState")
        if ts.get("active") and not ts.get("sent"):
            fix = g.call("autoUnits")
            time.sleep(0.5)
            r = g.call("endTurn")
            r["note"] = f"turn was un-readied by idle units; auto-handled: {fix}"
        return J(r)
    global _last_block
    now = g.call("turnState").get("turn")
    if _last_block == now:
        log = g.call("autoResolve", True)
        time.sleep(0.8)
        r2 = g.call("endTurn")
        for _ in range(4):  # blockers can chain (advisor warnings), and clearing one is applied asynchronously
            if r2.get("ok"):
                break
            log += g.call("unstick"); time.sleep(0.8)
            r2 = g.call("endTurn")
        out = {"forced": True, "autoLog": log, "result": r2}
        if not r2.get("ok"):
            out["next"] = ("Still blocked after forcing. Do NOT debug this with run_js: stop now and end your session. "
                           "The supervisor will clear the blocker and finish the turn.")
        return J(out)
    _last_block = now
    return J(r)


_last_block = None


@app.tool()
def close_popups() -> str:
    """Dismiss purely visual game screens (tech/civic unlocked, resolved narrative boxes, natural disaster /
    wonder cinematics). Safe: never discards a pending decision. Runs automatically each turn, but call it if
    the game seems stuck behind a popup."""
    return call("closePopups")


@app.tool()
def read_notes() -> str:
    """Read your persistent strategy notes (carried between turns and sessions)."""
    try:
        return open(NOTES, encoding="utf-8").read()
    except FileNotFoundError:
        return "(no notes yet)"


@app.tool()
def write_notes(content: str) -> str:
    """Overwrite your persistent strategy notes. Keep them concise (<60 lines): victory plan, current
    priorities, city plans, threats, diplomacy stance, lessons learned."""
    with open(NOTES, "w", encoding="utf-8") as f:
        f.write(content)
    return "saved"


@app.tool()
def log_journal(entry: str) -> str:
    """Append one line to the game journal (key decisions and why)."""
    turn = g.call("turnState").get("turn")
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(f"- T{turn}: {entry}\n")
    return "ok"


# ---------------- learning (lessons kept across games; settings: dashboard "Learning") ----------------
@app.tool()
def lookup_rules(query: str) -> str:
    """Look up the game's own rules text (Civilopedia data) for units, buildings, improvements, wonders, techs,
    civics, traditions, resources, governments, beliefs, legacy paths, terrain... by name, e.g. "Factory",
    "Settler", "Rail". Free and instant; use it before relying on a guess about what something does."""
    if learning.mode() == "off":
        return "learning tools are turned off in the settings"
    return call("lookupRules", query, 8)


@app.tool()
def remember_lesson(topic: str, lesson: str) -> str:
    """Save a lesson for ALL future turns and games (it is added to your instructions from then on).
    Use for non-obvious, reusable knowledge: a rule the game enforced, why an action failed and what works
    instead, a strategy that clearly paid off or backfired, a useful research finding. 1-2 general sentences;
    no turn-specific facts (those go in write_notes). topic: short heading, e.g. "Settling", "Combat", "Diplomacy"."""
    ts = g.call("turnState")
    ov = {}
    try:
        ov = g.call("overview")
    except Exception:
        pass
    age = str(ov.get("age") or "").replace("AGE_", "").title()
    return learning.remember(topic, lesson, f"T{ts.get('turn')} {age}".strip())


@app.tool()
def rewrite_lessons(content: str) -> str:
    """STRATEGY REVIEWS ONLY: replace the whole learned-lessons list with a curated version (merge duplicates,
    drop lessons proven wrong or obsolete, keep it under ~80 lines, grouped under '## Topic' headings)."""
    return learning.rewrite(content)


@app.tool()
def read_lessons() -> str:
    """The current learned-lessons list (it is already in your instructions; use this when curating)."""
    return learning.read_lessons()


@app.tool()
def research_strategy(question: str) -> str:
    """Research a Civ VII strategy question online (a short web-search session; costs money and is limited per
    age in the settings). Only for important questions your skills, lessons and lookup_rules don't answer, e.g.
    "How should I pick sites in the Distant Lands for treasure fleets?". Save what's worth keeping with remember_lesson."""
    ts = g.call("turnState")
    try:
        age = g.call("overview").get("age")
    except Exception:
        age = None
    return learning.research(question, age, ts.get("turn"))


@app.tool()
def write_chronicle(entry: str, headline: str = "") -> str:
    """Write this turn's chronicle entry, IN CHARACTER as our leader (1-3 vivid sentences about what happened
    and what you intend - like a royal diary / historian's account; no game jargon like tool names or ids).
    headline: optional short banner for truly big moments only (first contact, war, peace, city founded or
    captured, wonder, new age, victory progress)."""
    ts = g.call("turnState")
    rec = {"turn": ts.get("turn"), "time": time.strftime("%H:%M:%S"), "entry": entry.strip()[:1200],
           "headline": headline.strip()[:120]}
    try:  # snapshot of the game at this moment (camera is on the turn's main action) for the chronicle video
        import media
        shot = media.capture_game(os.path.join(media.FRAMES, f"T{int(rec['turn'] or 0):03d}.jpg"))
        if shot:
            rec["frame"] = os.path.basename(shot)
    except Exception:
        pass
    with open(CHRONICLE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return "recorded"


@app.tool()
def run_js(code: str) -> str:
    """Escape hatch: evaluate a JavaScript expression in the game's UI context (Players, Game, Cities, Units,
    GameplayMap, GameInfo, CB helpers...). Returns result as string/JSON. Use rarely, for info the tools lack."""
    return g.raw(code)


if __name__ == "__main__":
    app.run()
