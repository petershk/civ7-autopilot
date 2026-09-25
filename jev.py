"""Jev (defapi.org decisions API) as a cheap decision-maker for routine turns.

Jev can't act in the game; it only picks among options we give it. So a routine turn is played by
this module: it lists each routine decision (city production, town focus, population placement),
asks Jev to choose, applies the choice through the in-game CB library, lets the unit heuristics give
idle units orders, and ends the turn. Anything that isn't routine is handed back to a model session.

Brain spec: "jev" or "jev:<model>" (default model typesafe/jev-1.13). Key: DEFAPI_KEY.
"""
import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://api.defapi.org/api/v1/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
MAX_OPTIONS = 14  # keep choices focused; options are pre-sorted so the best candidates survive

# decisions a routine turn can resolve; anything else pending means the turn needs a model session
ROUTINE = {"CHOOSE_CITY_PRODUCTION", "CHOOSE_TOWN_PROJECT", "NEW_POPULATION", "UNIT_PROMOTION_AVAILABLE",
           "UNIT_PROMOTION", "ASSIGN_NEW_RESOURCES", "COMMAND_UNITS", "DIPLOMATIC_ACTION_ESPIONAGE", "DIPLOMATIC_ACTION_AGENDA"}


def api_key():
    """DEFAPI_KEY from this process, else from the saved Windows user environment (setx), which
    processes started before the key was set don't inherit."""
    k = os.environ.get("DEFAPI_KEY")
    if k:
        return k.strip()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return str(winreg.QueryValueEx(h, "DEFAPI_KEY")[0]).strip()
    except (ImportError, OSError):
        return ""


def available():
    return bool(api_key())


def model_of(spec):
    _, _, m = (spec or "jev").partition(":")
    return m or DEFAULT_MODEL


def decide(state, questions, model=DEFAULT_MODEL, timeout=60):
    """questions: {key: {"type": "choice", "instructions": str, "criteria": {option: description}}}.
    Returns (answers, cost_usd)."""
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jev HTTP {e.code}: {e.read()[:300]!r}")
    return data.get("answers", {}), float(data.get("consumed") or 0)


def choose(state, instructions, options, model):
    """options: {key: description}. Returns (chosen_key, probability, cost)."""
    ans, cost = decide(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}}, model)
    a = ans.get("q", {})
    pick = a.get("choice")
    return pick, (a.get("probabilities") or {}).get(pick), cost


# ------------------------------------------------------------------ game state for Jev
def game_state(brief, notes):
    """Compact situation summary: what Jev judges each choice against."""
    ov = brief.get("overview") if isinstance(brief.get("overview"), dict) else {}
    st = brief.get("standings") if isinstance(brief.get("standings"), dict) else {}
    me = next((p for p in st.get("players", []) if p.get("me")), {})
    near = brief.get("nearbyForeignUnits") if isinstance(brief.get("nearbyForeignUnits"), list) else []
    return {
        "game": "Civilization VII",
        "turn": ov.get("turn"), "age": ov.get("age"), "ageProgress": ov.get("ageProgress"),
        "we_are": ov.get("me"), "yields_per_turn": ov.get("yields"), "gold": ov.get("gold"),
        "influence": ov.get("influence"), "cities": ov.get("cities"), "government": ov.get("government"),
        "researching": ov.get("research"), "civic": ov.get("civic"),
        "legacy_paths": ov.get("legacyPaths"), "at_war_with": ov.get("atWarWith"),
        "our_ranks_vs_rivals": st.get("ranks") or {k: me.get(k) for k in ("sci", "cult", "gold", "happy", "settlements")},
        "hostile_units_nearby": sum(1 for u in near if u.get("hostile")),
        "strategy_plan": (notes or "")[:1800],
    }


# ------------------------------------------------------------------ routine turn
def _fmt(e, kind):
    bits = [e.get("name") or e.get("key"), f"({kind})"]
    if e.get("turns") is not None:
        bits.append(f"{e['turns']} turns")
    if e.get("desc"):
        bits.append("- " + e["desc"])
    return " ".join(str(b) for b in bits)


def _production(g, state, city, name, model, out):
    bo = g.call("buildOptions", city)
    if not isinstance(bo, dict) or bo.get("error"):
        return False
    opts = {}
    for e in sorted(bo.get("buildings", []), key=lambda e: e.get("turns") or 99):
        if not e.get("repair"):
            opts.setdefault(e["key"], _fmt(e, e.get("cls") or "building"))
    for e in sorted(bo.get("units", []), key=lambda e: e.get("turns") or 99):
        opts.setdefault(e["key"], _fmt(e, "unit"))
    for e in bo.get("projects", []):
        opts.setdefault(e["key"], _fmt(e, "project"))
    opts = dict(list(opts.items())[:MAX_OPTIONS])
    if not opts:
        return False
    pick, p, cost = choose(dict(state, city=name, city_details=_city(g, city)),
                           f"City {name} has an empty production queue. What should it build next to best serve our strategy?",
                           opts, model)
    out["cost"] += cost
    r = g.call("build", city, pick) if pick in opts else {"ok": False}
    out["decisions"].append({"what": f"{name} builds", "pick": opts.get(pick, pick), "p": p, "ok": bool(r.get("ok")),
                             "options": len(opts)})
    return bool(r.get("ok"))


def _town_focus(g, state, city, name, model, out):
    fo = g.call("townFocusOptions", city)
    if not isinstance(fo, list) or not fo:
        return False
    opts = {f["key"]: f"{f.get('name')}" + (f" - {f['desc']}" if f.get("desc") else "") for f in fo[:MAX_OPTIONS]}
    pick, p, cost = choose(dict(state, town=name, town_details=_city(g, city)),
                           f"Town {name} needs a focus. Which focus best serves our strategy?", opts, model)
    out["cost"] += cost
    r = g.call("setTownFocus", city, pick) if pick in opts else {"ok": False}
    if pick == "GROWTH":
        r = {"ok": True}  # growth is the default; nothing to send
    out["decisions"].append({"what": f"{name} focus", "pick": opts.get(pick, pick), "p": p, "ok": bool(r.get("ok"))})
    return bool(r.get("ok"))


def _population(g, state, model, out):
    go = g.call("growthOptions", None)
    if not isinstance(go, dict) or go.get("error"):
        return False
    opts, where = {}, {}
    for e in go.get("expand", [])[:10]:
        k = f"expand_{e['at'][0]}_{e['at'][1]}"
        opts[k] = f"work new tile {e['at']}: yields {e.get('yields')}" + (f", {e['improvement']}" if e.get("improvement") else "") + \
                  (f", resource {e['resource']}" if e.get("resource") else "")
        where[k] = (e["at"], False)
    for e in go.get("specialists", [])[:4]:
        k = f"specialist_{e['at'][0]}_{e['at'][1]}"
        opts[k] = f"specialist in district {e['at']} ({e.get('workers')} slots used): gains {e.get('gain')}"
        where[k] = (e["at"], True)
    if not opts:
        return False
    name = go.get("city")
    pick, p, cost = choose(dict(state, city=name), f"{name} grew. Where should the new citizen work?", opts, model)
    out["cost"] += cost
    if pick not in where:
        return False
    (x, y), spec = where[pick]
    r = g.call("placePop", go.get("cityId"), x, y, spec)
    out["decisions"].append({"what": f"{name} new citizen", "pick": opts[pick], "p": p, "ok": bool(r.get("ok"))})
    return bool(r.get("ok"))


def _city(g, city):
    try:
        c = next((c for c in g.call("cities") if c.get("id") == city or c.get("name") == city), None)
        return {k: c.get(k) for k in ("name", "town", "capital", "pop", "yields", "growthIn")} if c else None
    except Exception:
        return None


# ------------------------------------------------------------------ everything else ("Jev plays everything")
# Each decision: describe the situation, list what the game allows, let Jev pick, apply it through CB.
def _ask(g, state, out, model, what, instructions, opts, apply, extra=None, limit=MAX_OPTIONS):
    """opts: {key: description}. apply(key) -> CB result. Records the decision; returns True if applied."""
    opts = dict(list(opts.items())[:limit])
    if not opts:
        return False
    if len(opts) == 1:  # nothing to decide
        pick, p, cost = next(iter(opts)), 1.0, 0.0
    else:
        pick, p, cost = choose(dict(state, **(extra or {})), instructions, opts, model)
    out["cost"] += cost
    r = apply(pick) if pick in opts else {"ok": False, "error": f"Jev picked an unknown option {pick!r}"}
    ok = bool(r.get("ok", True)) if isinstance(r, dict) else bool(r)
    out["decisions"].append({"what": what, "pick": opts.get(pick, pick), "p": p, "ok": ok, "options": len(opts)})
    return ok


def _node_opts(nodes):
    return {n["key"]: f"{n.get('name')}" + (f", {n['turns']} turns" if n.get("turns") is not None else "")
            + (f" - unlocks {', '.join(n['unlocks'][:6])}" if n.get("unlocks") else "") for n in nodes or []}


def _desc_opts(items, name="name"):
    return {i["key"]: f"{i.get(name) or i['key']}" + (f" - {i['desc']}" if i.get("desc") else "") for i in items or []}


def _list(r, key=None):
    r = (r or {}).get(key) if key and isinstance(r, dict) else r
    return r if isinstance(r, list) else []


# pending type -> (question, options from the game, how to apply a pick)
CHOICES = {
    "CHOOSE_TECH": ("Choose our next technology to research.",
                    lambda g: _node_opts(_list(g.call("techOptions"))), lambda g, k: g.call("setTech", k)),
    "CHOOSE_CULTURE_NODE": ("Choose our next civic to study.",
                            lambda g: _node_opts(_list(g.call("civicOptions"))), lambda g, k: g.call("setCivic", k)),
    "CHOOSE_GOVERNMENT": ("Choose our government.",
                          lambda g: {o["key"]: f"{o['name']} - celebration bonuses: "
                                     + "; ".join(c for c in o.get("celebrationChoices", []) if c) for o in _list(g.call("governmentOptions"))},
                          lambda g, k: g.call("setGovernment", k)),
    "CHOOSE_GOLDEN_AGE": ("A celebration begins. Choose its bonus.",
                          lambda g: _desc_opts(_list(g.call("celebrationOptions"))), lambda g, k: g.call("chooseCelebration", k)),
    "CHOOSE_PANTHEON": ("Choose our pantheon belief.",
                        lambda g: _desc_opts(_list(g.call("pantheonOptions"))), lambda g, k: g.call("choosePantheon", k)),
    "CHOOSE_RELIGION": ("Choose the religion to found.",
                        lambda g: _desc_opts(_list(g.call("religionOptions"), "religions")), lambda g, k: g.call("foundReligion", k)),
    "CHOOSE_BELIEF": ("Choose a belief for our religion.",
                      lambda g: _desc_opts(_list(g.call("beliefOptions"))), lambda g, k: g.call("addBelief", k)),
    "CAN_BUY_ATTRIBUTE_SKILL": ("Spend a leader attribute point.",
                                lambda g: {n["key"]: f"{n.get('tree', '').title()} tree, node {n['key'].rsplit('_', 1)[-1]}"
                                           + (f": {n['name']}" if n.get("name") else "") + (f" - {n['desc']}" if n.get("desc") else "")
                                           for n in _list(g.call("attributeOptions"), "nodes")},
                                lambda g, k: g.call("buyAttribute", k)),
    "CHOOSE_CITY_STATE_BONUS": ("A city-state now owes us. Choose the bonus it gives.",
                                lambda g: _desc_opts(_list(g.call("cityStateBonusOptions"), "options")),
                                lambda g, k: g.call("chooseCityStateBonus", k)),
    "CHOOSE_CIVILIZATION": ("The age is ending. Choose the civilization we become next.",
                            lambda g: _desc_opts(_list(g.call("nextCivOptions"))), lambda g, k: g.call("chooseNextCiv", k)),
}
NARRATIVE = {"CHOOSE_NARRATIVE_STORY_DIRECTION", "CHOOSE_DISCOVERY_STORY_DIRECTION", "CHOOSE_AUTO_NARRATIVE_STORY_DIRECTION"}


def _narrative(g, state, model, out):
    n = g.call("narrative")
    if not isinstance(n, dict) or not n.get("options"):
        return False
    opts = {str(i): f"{o.get('text') or o['key']}" + (f" (reward: {o['reward']})" if o.get("reward") else "")
            + ("" if o.get("affordable", True) else " [can't afford]") for i, o in enumerate(n["options"])}
    return _ask(g, state, out, model, f"event: {n.get('title')}", f"A story event: {n.get('title')}. {n.get('body', '')} "
                "Which response best serves our strategy?", opts, lambda k: g.call("chooseNarrative", int(k)))


def _policies(g, state, model, out):
    for _ in range(6):  # one empty slot at a time
        pol = g.call("policies")
        if not isinstance(pol, dict):
            break
        free = sum((pol.get("slots") or {}).values()) - len(pol.get("active") or [])
        avail = [a for a in pol.get("available") or [] if a["key"] not in {x["key"] for x in pol.get("active") or []}]
        if free <= 0 or not avail:
            break
        if not _ask(g, state, out, model, "policy", "A policy slot is empty. Which policy should fill it?",
                    {a["key"]: f"{a['name']} ({a.get('kind')})" + (f" - {a['desc']}" if a.get("desc") else "") for a in avail},
                    lambda k: g.call("setPolicy", k, True)):
            break
    g.call("policiesDone")
    return True


def _diplomacy(g, state, model, out):
    d = g.call("diploPending")
    if not isinstance(d, dict):
        return
    for s in d.get("statements") or []:
        who = s.get("fromName") or s.get("from")
        if "FIRST_MEET" in str(s.get("stmt")) or s.get("stmt") == "GREETING":
            _ask(g, state, out, model, f"first meeting {who}", f"We just met {who}. How should we greet them? "
                 "Friendly helps relations; unfriendly signals we may be rivals.",
                 {"FRIENDLY": "friendly", "NEUTRAL": "neutral", "UNFRIENDLY": "unfriendly"},
                 lambda k, s=s: g.call("respondFirstMeet", s.get("sessionId", -1), s.get("from"), k))
        elif s.get("dealItems"):
            _ask(g, state, out, model, f"deal from {who}", f"{who} proposes a deal: {s['dealItems']}. Accept it?",
                 {"accept": "accept the deal", "reject": "reject the deal"},
                 lambda k, s=s: g.call("answerDeal", s.get("from"), k == "accept"))
    for r in d.get("responses") or []:
        _ask(g, state, out, model, f"{r.get('title')} from {r.get('from')}",
             f"{r.get('from')}: {r.get('title')}. {r.get('request') or ''} {r.get('desc') or ''} How do we respond?",
             {str(i): f"{o.get('name')}" + (f" - {o['desc']}" if o.get("desc") else "") for i, o in enumerate(r.get("options") or [])},
             lambda k, r=r: g.call("respondAction", r["actionId"], int(k)))
    if d.get("callToArms"):
        _ask(g, state, out, model, "call to arms", f"An ally at war asks us to join: {json.dumps(d['callToArms'])[:400]}. Join?",
             {"join": "join the war (keeps the alliance)", "decline": "decline (ends the alliance)"},
             lambda k: g.call("answerCallToArms", k == "join"))


def _dist(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _units(g, state, model, out):
    """Settler destinations and what combat units near enemies do. Everything else: heuristics."""
    units = g.call("units")
    if not isinstance(units, list):
        return
    targets = g.raw("JSON.stringify(CB.settlerTargets||{})")
    try:
        targets = json.loads(targets)
    except ValueError:
        targets = {}
    ready = set(g.call("readyUnits") or [])
    for u in units:
        if u.get("id") not in ready or "SETTLER" not in str(u.get("type")) or u.get("id") in targets:
            continue
        spots = g.call("settleSpots", u["id"], 5)
        spots = spots if isinstance(spots, list) else []
        opts = {f"{s['at'][0]}_{s['at'][1]}": f"site {s['at']}, {s.get('dist')} tiles away: {s.get('why') or 'no notes'}" for s in spots}
        _ask(g, state, out, model, "settler destination", "Where should this settler found our next settlement?", opts,
             lambda k, u=u: g.raw(f"CB.settlerTargets[{json.dumps(u['id'])}]=[{k.replace('_', ',')}];'ok'") == "ok")
    enemies = [e for e in (g.call("enemiesNear", 8) or []) if isinstance(e, dict) and e.get("hostile")]
    cities = g.call("cities") or []
    for u in units:
        if u.get("id") not in ready or not u.get("canAttack") or not u.get("at"):
            continue
        near = sorted((e for e in enemies if _dist(u["at"], e["at"]) <= 3), key=lambda e: _dist(u["at"], e["at"]))[:5]
        if not near:
            continue
        opts, act = {}, {}
        for e in near:
            k = f"attack_{e['at'][0]}_{e['at'][1]}"
            opts[k] = (f"attack the {e.get('who')} {e.get('type')} at {e['at']} ({_dist(u['at'], e['at'])} tiles; "
                       f"its health {e.get('hp')}, our health {u.get('hp')}, our strength {u.get('str')})")
            act[k] = lambda e=e, u=u: g.call("moveTo", u["id"], e["at"][0], e["at"][1])
        opts["hold"] = "hold position (fortify)"
        act["hold"] = lambda u=u: g.call("hold", u["id"])
        home = min((c for c in cities if c.get("at")), key=lambda c: _dist(u["at"], c["at"]), default=None)
        if home and _dist(u["at"], home["at"]) > 0:
            opts["fall_back"] = f"fall back to {home.get('name')} at {home['at']} to defend it"
            act["fall_back"] = lambda u=u, home=home: g.call("moveTo", u["id"], home["at"][0], home["at"][1])
        _ask(g, state, out, model, f"{u.get('type')} near enemies",
             f"Our {u.get('type')} at {u['at']} (health {u.get('hp')}, strength {u.get('str')}) has hostile units nearby. "
             "What should it do? Attack only if it's likely to win or protects a city.", opts, lambda k: act[k]())


def play_full_turn(g, turn, brief, notes, spec="jev", log=print):
    """'Jev plays everything': every decision with options goes to Jev; whatever Jev can't express falls to the
    heuristics, so the turn always ends. Returns the same dict as play_routine_turn (never hands off by itself)."""
    model = model_of(spec)
    state = game_state(brief, notes)
    out = {"ended": False, "cost": 0.0, "decisions": [], "handoff": None, "errors": []}

    def safely(fn, *a):
        try:
            fn(*a)
        except Exception as e:
            out["errors"].append(f"{getattr(fn, '__name__', fn)}: {e}")

    safely(_diplomacy, g, state, model, out)
    done = set()
    for _ in range(30):
        pend = g.call("pending")
        items = pend.get("items", []) if isinstance(pend, dict) else []
        item = next((i for i in items if (i.get("type"), i.get("city")) not in done and
                     (i.get("type") in CHOICES or i.get("type") in NARRATIVE or i.get("type") in
                      ("CHOOSE_CITY_PRODUCTION", "CHOOSE_TOWN_PROJECT", "NEW_POPULATION", "TRADITIONS_AVAILABLE"))), None)
        if not item:
            break
        t = item["type"]
        done.add((t, item.get("city")))
        try:
            if t == "CHOOSE_CITY_PRODUCTION":
                _production(g, state, item.get("city"), item.get("cityName"), model, out)
            elif t == "CHOOSE_TOWN_PROJECT":
                _town_focus(g, state, item.get("city"), item.get("cityName"), model, out)
            elif t == "NEW_POPULATION":
                if _population(g, state, model, out):
                    done.discard((t, item.get("city")))
            elif t == "TRADITIONS_AVAILABLE":
                _policies(g, state, model, out)
            elif t in NARRATIVE:
                if _narrative(g, state, model, out):
                    done.discard((t, item.get("city")))  # several events can queue up
            else:
                q, opts, apply = CHOICES[t]
                _ask(g, state, out, model, t.replace("CHOOSE_", "").replace("_", " ").lower(), q, opts(g), lambda k: apply(g, k),
                     limit=40 if t == "CHOOSE_CIVILIZATION" else MAX_OPTIONS)  # every valid civ, not the first 14
        except Exception as e:
            out["errors"].append(f"{t}: {e}")
        time.sleep(0.4)
    safely(_units, g, state, model, out)
    time.sleep(0.5)
    # anything left (unit orders, promotions, resources, dismissals): the heuristics
    auto = g.call("autoResolve", True)
    time.sleep(0.8)
    out["auto"], ended, ts = _end_turn(g, turn, auto)
    out["ended"] = ended
    if not ended:
        out["handoff"] = f"still blocked after Jev's turn ({ts.get('blocking')})"
    return out


def _end_turn(g, turn, auto):
    """End the turn, re-ordering units that wake up during the attempt (a scout whose exploring stops at a
    discovery, a queued move that breaks off) for a few rounds. Returns (log, ended, turnState)."""
    auto = auto if isinstance(auto, list) else [auto]
    ts = {}
    for attempt in range(4):
        if attempt:
            auto += (g.call("autoResolve", True) or []) + (g.call("unstick") or [])
            time.sleep(1.0)
        g.call("endTurn")
        time.sleep(1.5)
        ts = g.call("turnState")
        if not (ts.get("turn") == turn and ts.get("active") and not ts.get("sent")):
            return auto, True, ts
    return auto, False, ts


def not_routine(pending):
    """Pending decision types a routine turn can't handle (empty = routine)."""
    items = pending.get("items", []) if isinstance(pending, dict) else []
    return sorted({i.get("type", "?") for i in items if i.get("type") not in ROUTINE and not str(i.get("type", "")).startswith("ADVISOR")})


def play_routine_turn(g, turn, brief, notes, spec="jev", log=print):
    """Resolve routine decisions with Jev, units with heuristics, then end the turn.
    Returns {"ended": bool, "cost": float, "decisions": [...], "handoff": reason or None}."""
    model = model_of(spec)
    state = game_state(brief, notes)
    out = {"ended": False, "cost": 0.0, "decisions": [], "handoff": None, "errors": []}
    done = set()
    for _ in range(25):
        pend = g.call("pending")
        other = not_routine(pend)
        if other:
            out["handoff"] = "needs a model: " + ",".join(other)
            return out
        items = pend.get("items", []) if isinstance(pend, dict) else []
        item = next((i for i in items if i.get("type") in ("CHOOSE_CITY_PRODUCTION", "CHOOSE_TOWN_PROJECT", "NEW_POPULATION")
                     and (i.get("type"), i.get("city")) not in done), None)
        if not item:
            break
        done.add((item.get("type"), item.get("city")))
        try:
            if item["type"] == "CHOOSE_CITY_PRODUCTION":
                ok = _production(g, state, item.get("city"), item.get("cityName"), model, out)
            elif item["type"] == "CHOOSE_TOWN_PROJECT":
                ok = _town_focus(g, state, item.get("city"), item.get("cityName"), model, out)
            else:
                ok = _population(g, state, model, out)
                if ok:
                    done.discard((item.get("type"), item.get("city")))  # several cities can grow at once
        except Exception as e:
            ok = False
            out["errors"].append(f"{item['type']}: {e}")
        if not ok:
            out["errors"].append(f"could not resolve {item['type']} {item.get('cityName') or ''}".strip())
        time.sleep(0.4)
    # promotions, resource assignment and unit orders: existing heuristics (they don't touch the choices above)
    auto = g.call("autoResolve", True)
    time.sleep(0.8)
    out["auto"], out["ended"], ts = _end_turn(g, turn, auto)
    if not out["ended"]:
        out["handoff"] = f"still blocked after routine play ({ts.get('blocking')})"
    return out
