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
    r = g.call("endTurn")
    if not r.get("ok"):
        auto = (auto if isinstance(auto, list) else [auto]) + g.call("unstick")
        time.sleep(1.2)
        r = g.call("endTurn")
    time.sleep(1.5)
    ts = g.call("turnState")
    out["ended"] = not (ts.get("turn") == turn and ts.get("active") and not ts.get("sent"))
    out["auto"] = auto
    if not out["ended"]:
        out["handoff"] = f"still blocked after routine play ({ts.get('blocking')})"
    return out
