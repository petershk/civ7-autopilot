"""Live dashboard for the Civ VII autopilot: http://localhost:8777

Reads the autopilot's files (logs/, state/) and polls the game lightly through the tuner.
Run: python dashboard.py   (run_autopilot.bat starts it automatically)
"""
import glob
import json
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import brains
import control
import jev
import learning
from game import Game

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
STATE = os.path.join(HERE, "state")
PORT = int(os.environ.get("CIV_DASH_PORT", "8777"))

_game = None
_cache = {"t": 0, "live": None, "err": None}
_lock = threading.Lock()


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def live_game():
    """Overview/cities/units from the running game, cached for 8 seconds."""
    global _game
    with _lock:
        if time.time() - _cache["t"] < 8:
            return _cache["live"], _cache["err"]
        try:
            if _game is None:
                _game = Game()
            live = {"overview": _game.call("overview"), "cities": _game.call("cities"),
                    "units": _game.call("units"), "players": _game.call("players"),
                    "rankings": _game.call("rankings")}
            _cache.update(t=time.time(), live=live, err=None)
        except Exception as e:  # game closed / loading / between ages
            _game = None
            _cache.update(t=time.time(), err=str(e))
        return _cache["live"], _cache["err"]


_icache = {"t": 0, "data": None}
_mcache = {"t": 0, "data": None}
_tcache = {"t": 0, "data": None}
_bcache = {"t": 0, "data": None}


def trees():
    global _game
    with _lock:
        if time.time() - _tcache["t"] < 10 and _tcache["data"] is not None:
            return _tcache["data"]
        try:
            if _game is None:
                _game = Game()
            data = _game.call("trees")
        except Exception as e:
            _game = None
            data = {"error": str(e)}
        _tcache.update(t=time.time(), data=data)
        return data


def brain_list():
    if time.time() - _bcache["t"] > 60 or _bcache["data"] is None:
        extra = [{"spec": "jev", "label": "Jev (picks options; rules move units) - routine turns only", "provider": "jev"}] if jev.available() else []
        _bcache.update(t=time.time(), data=brains.available() + extra)
    return _bcache["data"]


def world_map():
    global _game
    with _lock:
        if time.time() - _mcache["t"] < 6 and _mcache["data"] is not None:
            return _mcache["data"]
        try:
            if _game is None:
                _game = Game()
            data = _game.call("worldMap")
        except Exception as e:
            _game = None
            data = {"error": str(e)}
        _mcache.update(t=time.time(), data=data)
        return data


VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural",
          "en-US-GuyNeural", "en-US-DavisNeural", "en-GB-SoniaNeural", "en-US-AriaNeural"]
TTS_DIR = os.path.join(STATE, "tts")


def _old_tts_mp3(text, voice):
    """(superseded by media.tts_mp3)"""
    import asyncio
    import hashlib
    import edge_tts
    voice = voice if voice in VOICES else VOICES[0]
    os.makedirs(TTS_DIR, exist_ok=True)
    path = os.path.join(TTS_DIR, hashlib.sha1((voice + "|" + text).encode("utf-8")).hexdigest()[:16] + ".mp3")
    if not os.path.exists(path):
        asyncio.run(edge_tts.Communicate(text, voice, rate="-6%", pitch="-3Hz").save(path + ".tmp"))
        os.replace(path + ".tmp", path)
    with open(path, "rb") as f:
        return f.read()


_hcache = {"t": 0, "data": None}
EVENT_KEYS = ("cities", "kills", "buildings", "wonders")   # recorded as per-turn events -> running totals
LEVEL_KEYS = ("sci", "cult", "gold", "techs")               # per-turn levels -> carry forward gaps


def full_history():
    """Game's own per-turn stats since turn 1 (known civs only), merged with our recorded rankings."""
    global _game
    with _lock:
        if time.time() - _hcache["t"] < 20 and _hcache["data"] is not None:
            return _hcache["data"]
        try:
            if _game is None:
                _game = Game()
            summ = _game.call("summaryHistory")
        except Exception:
            _game = None
            summ = {"rows": []}
    ours = {r["turn"]: {p["id"]: p for p in r.get("players", [])} for r in jsonl(os.path.join(STATE, "history.jsonl"))}
    rows, last, total = [], {}, {}
    for r in summ.get("rows", []) if isinstance(summ, dict) else []:
        out = []
        for p in r["players"]:
            pid = p["id"]
            lp = last.setdefault(pid, {})
            tp = total.setdefault(pid, {k: 0 for k in EVENT_KEYS})
            q = {"id": pid, "name": p["name"], "me": p["me"]}
            for k in LEVEL_KEYS:
                if p.get(k) is not None:
                    lp[k] = p[k]
                q[k] = lp.get(k)
            for k in EVENT_KEYS:
                tp[k] += p.get(k) or 0
                q[k] = tp[k]
            extra = ours.get(r["turn"], {}).get(pid)
            if extra:
                for k in ("happy", "settlements", "legacyTotal", "infl"):
                    if extra.get(k) is not None:
                        q[k] = extra[k]
            out.append(q)
        rows.append({"turn": r["turn"], "players": out})
    if not rows:  # fall back to our own recordings
        rows = jsonl(os.path.join(STATE, "history.jsonl"))
    _hcache.update(t=time.time(), data=rows)
    return rows


VIDEO_JOB = {"running": False, "pct": 0, "msg": "", "file": None, "error": None}


def start_video(opts):
    if VIDEO_JOB["running"]:
        return VIDEO_JOB

    def work():
        import video
        VIDEO_JOB.update(running=True, pct=0, msg="starting", file=None, error=None)
        try:
            out = video.build(int(opts.get("from") or 1), int(opts.get("to") or 10 ** 6), opts.get("voice") or "en-GB-RyanNeural",
                              lambda pct, msg: VIDEO_JOB.update(pct=pct, msg=msg),
                              premium_mode=bool(opts.get("premium")), illustrate=opts.get("illustrate") or "headlines",
                              gemini_voice=opts.get("geminiVoice") or "Charon",
                              animate=opts.get("animate") or "none", style=opts.get("style") or "painted")
            VIDEO_JOB.update(file=os.path.basename(out), msg="done")
        except Exception as e:
            VIDEO_JOB.update(error=str(e)[:500], msg="failed")
        finally:
            VIDEO_JOB["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return VIDEO_JOB


def videos():
    vd = os.path.join(STATE, "videos")
    return sorted((os.path.basename(f) for f in glob.glob(os.path.join(vd, "chronicle_*.mp4"))), reverse=True)


def jsonl(path, last=None):
    out = []
    for line in read(path).splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out[-last:] if last else out


def intel():
    global _game
    with _lock:
        if time.time() - _icache["t"] < 10 and _icache["data"] is not None:
            return _icache["data"]
        try:
            if _game is None:
                _game = Game()
            data = {"civs": _game.call("intel"), "rankings": _game.call("rankings")}
        except Exception as e:
            _game = None
            data = {"error": str(e)}
        _icache.update(t=time.time(), data=data)
        return data


def session_feed(path, limit=80):
    """Turn a stream-json transcript into readable feed items."""
    items, names = [], {}
    for line in read(path).splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type")
        if t == "assistant":
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    items.append({"k": "think", "text": c["text"].strip()})
                elif c.get("type") == "tool_use":
                    name = c["name"].replace("mcp__civ7__", "")
                    names[c.get("id")] = name
                    args = json.dumps(c.get("input", {}), ensure_ascii=False)
                    items.append({"k": "tool", "name": name, "text": "" if args == "{}" else args})
        elif t == "user":
            for c in ev.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    x = c.get("content")
                    x = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
                    m = re.match(r'^\{"result":"(.*)"\}$', x, re.S)
                    if m:
                        try:
                            x = json.loads('"' + m.group(1) + '"')
                        except ValueError:
                            pass
                    items.append({"k": "result", "name": names.get(c.get("tool_use_id"), ""), "text": x[:4000]})
        elif t == "result":
            items.append({"k": "done", "text": ev.get("result", ""), "cost": ev.get("total_cost_usd")})
    return items[-limit:]


SESSION_RE = re.compile(r"\[(.*?)\] (turn|review|jev|research)_T(\d+)(_retry)?: rc=(\S+) tools=(\d+) (\d+)s cost=(\S+)(?: brain=(\S+))? :: (.*)")


def cost_report():
    """Cost of every turn, split by who did the work (from autopilot.log). Turn numbers restart each
    age, so a big drop in turn number starts a new age segment."""
    turns, seg, prev = [], 0, None
    for line in read(os.path.join(LOGS, "autopilot.log")).splitlines():
        m = SESSION_RE.match(line)
        if not m:
            continue
        try:
            cost = float(m.group(8))
        except ValueError:
            cost = 0.0
        t = int(m.group(3))
        if prev is not None and t < prev - 5:
            seg += 1
        prev = t
        role = "retry" if m.group(4) else {"turn": "turn", "review": "review", "jev": "jev", "research": "research"}[m.group(2)]
        if not turns or turns[-1]["turn"] != t or turns[-1]["seg"] != seg:
            turns.append({"seg": seg, "turn": t, "date": m.group(1)[:16], "total": 0.0, "parts": []})
        turns[-1]["parts"].append({"role": role, "brain": m.group(9) or "claude", "cost": round(cost, 6), "tools": int(m.group(6)),
                                   "secs": int(m.group(7)), "handoff": role == "jev" and m.group(5) != "ok"})
        turns[-1]["total"] = round(turns[-1]["total"] + cost, 6)
    by_brain = {}
    for tr in turns:
        for p in tr["parts"]:
            b = by_brain.setdefault(f"{p['role']} · {p['brain']}", {"sessions": 0, "cost": 0.0})
            b["sessions"] += 1
            b["cost"] = round(b["cost"] + p["cost"], 6)
    avg = lambda xs: round(sum(x["total"] for x in xs) / len(xs), 4) if xs else None
    cur = [t for t in turns if t["seg"] == seg]
    return {"turns": turns, "byBrain": by_brain,
            "summary": {"last10": avg(turns[-10:]), "last25": avg(turns[-25:]), "thisAge": avg(cur), "all": avg(turns),
                        "total": round(sum(t["total"] for t in turns), 2), "turnsCounted": len(turns)}}


def skills_report():
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "skills", "*", "SKILL.md"))):
        text = read(path)
        meta = dict(re.findall(r"^(name|description):\s*(.+)$", text.split("---", 2)[1] if text.startswith("---") else "", re.M))
        out.append({"folder": os.path.basename(os.path.dirname(path)), "name": meta.get("name", ""),
                    "description": meta.get("description", ""), "chars": len(text)})
    try:
        usage = json.loads(read(os.path.join(STATE, "research_usage.json"), "{}") or "{}")
    except ValueError:
        usage = {}
    return {"skills": out, "lessons": read(learning.LESSONS, ""), "researchUsage": usage}


def api_state():
    sessions = sorted(glob.glob(os.path.join(LOGS, "*.jsonl")), key=os.path.getmtime)
    current = sessions[-1] if sessions else None
    history = []
    total_cost = 0.0
    for line in read(os.path.join(LOGS, "autopilot.log")).splitlines():
        m = re.match(r"\[(.*?)\] (turn|review|jev|research)_T(\d+)(?:_retry)?: rc=(\S+) tools=(\d+) (\d+)s cost=(\S+)(?: brain=(\S+))? :: (.*)", line)
        if m:
            try:
                total_cost += float(m.group(7))
            except ValueError:
                pass
            history.append({"time": m.group(1)[11:], "kind": m.group(2), "turn": int(m.group(3)), "tools": int(m.group(5)),
                            "secs": int(m.group(6)), "brain": m.group(8) or "claude", "summary": m.group(9).strip("'\"")[:400]})
        elif re.search(r"fallback|GAME OVER|error|launch|starting new game|Begin Game|age transition|menu", line, re.I):
            history.append({"time": line[12:20], "kind": "event", "summary": line[22:400]})
    live, err = live_game()
    running = bool(current and time.time() - os.path.getmtime(current) < 20 and '"type":"result"' not in read(current)[-4000:])
    return {
        "live": live, "liveError": err,
        "status": json.loads(read(os.path.join(STATE, "status.json"), "{}") or "{}"),
        "game": json.loads(read(os.path.join(STATE, "game.json"), "{}") or "{}"),
        "notes": read(os.path.join(STATE, "strategy_notes.md"), "(no notes yet)"),
        "journal": read(os.path.join(STATE, "journal.md")).splitlines()[-40:],
        "session": {"name": os.path.basename(current or "")[:-6], "running": running,
                    "feed": session_feed(current) if current else []},
        "history": history[-120:],
        "totalCost": round(total_cost, 2),
        "control": control.get(),
        "chronicle": jsonl(os.path.join(STATE, "chronicle.jsonl"), 80),
        "now": time.strftime("%H:%M:%S"),
    }


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>Civ VII Autopilot</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--fg:#e6e8ee;--dim:#8b93a7;--acc:#e3b341;--ok:#3fb950;--bad:#f85149;--blue:#58a6ff;--violet:#bc8cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:18px;align-items:center;padding:14px 20px;border-bottom:1px solid var(--line);background:#12151b;position:sticky;top:0;z-index:2}
h1{font-size:18px;margin:0;color:var(--acc);letter-spacing:.5px}
.pill{padding:3px 10px;border-radius:999px;background:var(--line);color:var(--dim);font-size:12px}
.pill.live{background:#12361f;color:var(--ok)}.pill.idle{background:#2d2a14;color:var(--acc)}.pill.off{background:#3a1618;color:var(--bad)}
.stats{display:flex;gap:14px;flex-wrap:wrap}.stat b{color:var(--fg)}.stat{color:var(--dim)}
main{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:14px;padding:14px 20px}
@media(max-width:1000px){main{grid-template-columns:1fr}}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
section h2{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--dim)}
.feed{max-height:62vh;overflow:auto;display:flex;flex-direction:column;gap:6px}
.it{flex:0 0 auto;padding:6px 9px;border-radius:7px;background:#1d212a;white-space:pre-wrap;word-break:break-word}
.it.think{border-left:3px solid var(--violet)}.it.tool{border-left:3px solid var(--blue);font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.it.result{border-left:3px solid var(--line);color:var(--dim);font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.4;max-height:4.3em;overflow:hidden;cursor:pointer;margin-left:14px}
.it.result.open{max-height:none}.it.result .rn{color:#6e7891}
.it.done{border-left:3px solid var(--ok)}
.tn{color:var(--blue);font-weight:600}
pre.notes{white-space:pre-wrap;margin:0;font:12.5px/1.5 ui-monospace,Consolas,monospace;max-height:40vh;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--dim);font-weight:500}
.hist{max-height:40vh;overflow:auto}.hist div{padding:4px 0;border-bottom:1px solid var(--line)}.hist .t{color:var(--acc);font-weight:600;margin-right:6px}
.hist .ev{color:var(--bad)}.muted{color:var(--dim)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.ctl{display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--dim)}
.ctl button{background:var(--line);color:var(--fg);border:1px solid #333a48;border-radius:7px;padding:4px 12px;cursor:pointer;font:inherit}
.btn{background:var(--line);color:var(--fg);border:1px solid #333a48;border-radius:7px;padding:3px 10px;cursor:pointer;font:inherit}
#tab-costs select{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px}
.ctl button.paused{background:#3a1618;color:var(--bad)}.ctl select{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px}
.chron{max-height:34vh;overflow:auto;display:flex;flex-direction:column;gap:8px}
.chron .e{font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.5;padding:6px 10px;border-left:3px solid var(--acc);background:#1d1b16}
.chron .e .t{font-family:system-ui;font-size:11px;color:var(--acc);letter-spacing:1px;text-transform:uppercase;display:block}
.chron .e .hl{font-family:system-ui;font-weight:700;color:#ffd97a;display:block;margin-bottom:2px}
#toast{position:fixed;left:50%;top:78px;transform:translate(-50%,-20px);opacity:0;transition:all .5s;z-index:9;pointer-events:none;
 background:linear-gradient(180deg,#3b2f0f,#231c09);border:1px solid #8a6d1d;color:#ffe29a;padding:14px 28px;border-radius:12px;font:700 20px Georgia,serif;box-shadow:0 10px 40px #000a;text-align:center;max-width:80vw}
#toast.show{opacity:1;transform:translate(-50%,0)}#toast small{display:block;font:400 12px system-ui;color:#c9b27a;margin-top:4px}
canvas#mapc{display:block;margin:0 auto;background:#0a0c10;border-radius:8px}
.chart svg{width:100%;height:210px}.chart .lg{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;margin-top:4px}.chart .lg i{display:inline-block;width:10px;height:3px;vertical-align:middle;margin-right:4px}

.tabs{display:flex;gap:4px}.tabs a{padding:5px 12px;border-radius:7px;color:var(--dim);cursor:pointer;user-select:none}.tabs a.on{background:var(--line);color:var(--fg)}
.civgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}
@media(max-width:520px){.civgrid{grid-template-columns:1fr}}
.civ h3{margin:0;font-size:16px}.civ .sub{color:var(--dim);font-size:12.5px;margin:2px 0 8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}.chip{padding:2px 8px;border-radius:999px;background:var(--line);font-size:12px}
.chip.good{background:#12361f;color:var(--ok)}.chip.bad{background:#3a1618;color:var(--bad)}.chip.warn{background:#2d2a14;color:var(--acc)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12.5px}.kv b{color:var(--dim);font-weight:500}
.civ h4{margin:10px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--dim)}
.pos{color:var(--ok)!important}.neg{color:var(--bad)!important}

.rk{display:inline-block;min-width:18px;padding:0 5px;border-radius:5px;font-weight:600;text-align:center}
.rk1{background:#12361f;color:var(--ok)}.rk2{background:#2d2a14;color:var(--acc)}.rkn{background:#3a1618;color:var(--bad)}
tr.me td{background:#1c2433}.bar{height:5px;background:var(--line);border-radius:3px;margin-top:2px}.bar i{display:block;height:5px;border-radius:3px;background:var(--blue)}

</style></head><body>
<header><h1>⚜ Civ VII Autopilot</h1><nav class="tabs"><a data-tab="overview" class="on">Overview</a><a data-tab="map">Map</a><a data-tab="charts">Charts</a><a data-tab="tree">Tech tree</a><a data-tab="civs">Civilizations</a><a data-tab="costs">Settings &amp; costs</a></nav><span id="state" class="pill">…</span><div class="stats" id="stats"></div><span class="muted" id="clock" style="margin-left:auto"></span>
<div class="ctl"><button id="btnPause">⏸ Pause</button>
<a id="whoPlays" title="Who plays what. Change it on the Settings &amp; costs tab" style="cursor:pointer;color:var(--dim)"></a></div></header>
<div id="toast"></div>
<main id="tab-overview">
 <div class="col">
  <section><h2 style="display:flex;align-items:center;gap:10px">Chronicle
   <span style="margin-left:auto;display:flex;gap:6px;align-items:center;text-transform:none;letter-spacing:0">
    <select id="vRange" style="background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px"><option value="all">whole game</option><option value="10">last 10 turns</option><option value="25">last 25 turns</option><option value="50">last 50 turns</option></select>
    <label id="premWrap" style="display:none;font-size:12px;color:#ffe29a" title="Gemini voice acting + Lyria original score + painted illustrations (uses your GEMINI_API_KEY)"><input type="checkbox" id="vPrem"> ✨ premium</label>
    <select id="vIll" style="display:none;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px" title="Painted illustrations"><option value="headlines">paint big moments</option><option value="all">paint every turn</option><option value="none">no paintings</option></select>
    <select id="vAnim" style="display:none;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px" title="Animated scenes (Veo): characters move, battles play out, ambient sound"><option value="none">no animation</option><option value="headlines">animate big moments</option><option value="all">animate every turn ($$$)</option></select>
    <select id="vStyle" style="display:none;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px" title="Visual style"><option value="painted">painted epic</option><option value="cinematic">cinematic realism</option></select>
    <select id="vGVoice" style="display:none;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px" title="Gemini narrator"></select>
    <button id="btnVideo" style="background:#3b2f0f;color:#ffe29a;border:1px solid #8a6d1d;border-radius:7px;padding:3px 10px;cursor:pointer">🎬 Generate video</button></span></h2>
   <div id="vStatus" class="muted" style="margin:-2px 0 8px;font-size:12.5px"></div>
   <div class="chron" id="chron"><span class="muted">The chronicle begins with the next turn…</span></div></section>
  <section><h2 id="feedTitle">Current session</h2><div class="feed" id="feed"></div></section>
  <section><h2>Turn history</h2><div class="hist" id="hist"></div></section>
 </div>
 <div class="col">
  <section><h2 id="standTitle">Standings vs rivals</h2><div id="standings"></div></section>
  <section><h2>Strategy notes</h2><pre class="notes" id="notes"></pre></section>
  <section><h2>Cities</h2><div id="cities"></div></section>
  <section><h2>Units</h2><div id="units"></div></section>
  <section><h2>Known players</h2><div id="players"></div></section>
  <section><h2>Journal</h2><div class="hist" id="journal"></div></section>
 </div>
</main>
<div id="tab-map" style="display:none;padding:14px 20px"><section><h2 id="mapTitle">World map (what we've seen)</h2><div style="overflow:auto"><canvas id="mapc"></canvas></div><div id="mapLegend" class="chips"></div></section></div>
<div id="tab-costs" style="display:none;padding:14px 20px"><div class="grid2">
 <div class="col"><section><h2>Who plays what</h2><div class="muted" style="margin-bottom:8px;font-size:12.5px">Changes apply from the next turn. "default" = the command-line setting.</div><table id="cfgTbl"></table></section></div>
 <div class="col"><section><h2>Watching the game</h2><table><tr><th style="width:150px">Follow camera</th><td><label><input type="checkbox" id="cFollow"> pan to each action</label><div class="muted" style="font-size:11.5px">the game camera moves to whatever the agent is doing</div></td></tr><tr><th style="width:150px">Linger after actions</th><td><select id="cDelay"><option value="0">0s</option><option value="0.5">0.5s</option><option value="1">1s</option><option value="2">2s</option><option value="3">3s</option></select><div class="muted" style="font-size:11.5px">pause after each move or build so you can see it on the map</div></td></tr><tr><th style="width:150px">Pause between turns</th><td><select id="cTurn"><option value="0">0s</option><option value="5">5s</option><option value="15">15s</option><option value="30">30s</option><option value="60">60s</option></select><div class="muted" style="font-size:11.5px">extra wait before the next turn starts</div></td></tr></table></section><section><h2>Learning</h2><table>
<tr><th style="width:150px">Learning</th><td><select id="cLearn"><option value="off">off</option><option value="ingame">in-game only (rules lookup + lessons)</option><option value="online">in-game + online research</option></select><div class="muted" style="font-size:11.5px">lessons it saves are added to its instructions in this and future games</div></td></tr>
<tr><th>Online research</th><td><select id="cResearch"><option value="1">1 per age</option><option value="3">3 per age</option><option value="5">5 per age</option><option value="10">10 per age</option></select><div class="muted" style="font-size:11.5px" id="researchUsed">each research session costs about $0.10-0.50</div></td></tr>
</table></section>
<section><h2>Narration</h2><table><tr><th style="width:150px">Read aloud</th><td><label><input type="checkbox" id="cTTS"> narrate the chronicle</label><div class="muted" style="font-size:11.5px">reads each new chronicle entry in this browser</div></td></tr><tr><th style="width:150px">Voice</th><td><select id="cVoice" title="Narrator voice"><option value="en-GB-RyanNeural">Ryan (UK)</option><option value="en-GB-ThomasNeural">Thomas (UK)</option><option value="en-US-AndrewNeural">Andrew (US)</option><option value="en-US-BrianNeural">Brian (US)</option><option value="en-US-ChristopherNeural">Christopher (US)</option><option value="en-US-GuyNeural">Guy (US)</option><option value="en-US-DavisNeural">Davis (US)</option><option value="en-GB-SoniaNeural">Sonia (UK)</option><option value="en-US-AriaNeural">Aria (US)</option></select> <button id="btnTest" class="btn" title="Hear the latest entry">▶ test</button><div class="muted" style="font-size:11.5px">saved in this browser only</div></td></tr></table></section></div>
</div>
<div style="margin-top:14px">
 <section><h2>Cost per turn</h2><div id="costTiles" class="chips" style="gap:18px;margin-bottom:6px"></div><div id="byBrain"></div></section>
</div>
<section style="margin-top:14px"><h2 style="display:flex;align-items:center;gap:10px">Cost per turn by role<span class="muted" id="costLbl" style="margin-left:auto;text-transform:none;letter-spacing:0"></span></h2>
 <svg id="costSvg" viewBox="0 0 1000 240" style="width:100%;height:240px"></svg><div class="lg" id="costLg" style="display:flex;gap:14px;font-size:12px"></div></section>
<section style="margin-top:14px"><h2>What it knows</h2><div id="skillList" class="muted" style="margin-bottom:8px"></div><pre class="notes" id="lessons" style="max-height:36vh"></pre></section>
<section style="margin-top:14px"><h2>Recent turns</h2><div style="max-height:46vh;overflow:auto"><table id="costTbl"></table></div></section>
</div>
<div id="tab-charts" style="display:none;padding:14px 20px"><div id="charts" class="civgrid"><span class="muted">collecting history — one point per turn from now on…</span></div></div>
<div id="tab-tree" style="display:none;padding:14px 20px"><section>
 <div class="chips" id="treeTabs" style="margin-bottom:8px"></div>
 <div style="overflow:auto"><svg id="treeSvg"></svg></div>
 <div class="chips" style="margin-top:6px"><span class="chip good">researched</span><span class="chip" style="background:#123046;color:#58a6ff">researching now</span><span class="chip warn">available</span><span class="chip" style="background:#10302e;color:#39c5cf">mastery available</span><span class="chip">locked</span><span class="chip">★ agent's target</span></div>
 <div id="treeInfo" class="muted" style="margin-top:8px;min-height:3em">Click a node for details.</div></section></div>
<div id="tab-civs" style="display:none;padding:14px 20px"><div id="civs" class="civgrid"><span class="muted">loading…</span></div></div>
<script>
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let stick=true;const openSet=new Set();let lastSession=null;
document.addEventListener('click',e=>{const r=e.target.closest('.it.result');if(!r)return;const i=+r.dataset.i;openSet.has(i)?openSet.delete(i):openSet.add(i);r.classList.toggle('open')});$('feed').addEventListener('scroll',e=>{const f=e.target;stick=f.scrollTop+f.clientHeight>=f.scrollHeight-30});
async function tick(){
 let d;try{d=await (await fetch('/api/state')).json()}catch(e){$('state').className='pill off';$('state').textContent='dashboard offline';return}
 window.lastAge=(d.status&&d.status.age)||'';
 {const nd=(d.status&&d.status.defaults)||{};if(JSON.stringify(nd)!=JSON.stringify(DEFS)){DEFS=nd;if(BRAINS.length)loadBrains()}}
 const o=d.live&&d.live.overview||d.status.overview||{};
 const st=$('state');
 if(d.session.running){st.className='pill live';st.textContent='● agent playing '+d.session.name.replace('_',' ')}
 else if(d.liveError){st.className='pill off';st.textContent='game not reachable (loading?)'}
 else{st.className='pill idle';st.textContent=o.turnState&&o.turnState.active?'between sessions':'AI players moving'}
 const y=o.yields||{};
 $('stats').innerHTML=[['Turn',o.turn],['Age',(o.age||'').replace('AGE_','')],['Leader',o.me],['Cities',o.cities],['Gold',o.gold],['Influence',o.influence],
  ['Food',y.food],['Prod',y.prod],['Sci',y.sci],['Cult',y.cult],['Happy',y.happy],['Age progress',o.ageProgress],['Research',o.research&&o.research.node],['Civic',o.civic&&o.civic.node],['Spend',d.totalCost?'$'+d.totalCost:null]]
  .filter(x=>x[1]!=null&&x[1]!=='').map(x=>`<span class="stat">${x[0]} <b>${esc(x[1])}</b></span>`).join('');
 if(o.legacyPaths){$('stats').innerHTML+=Object.entries(o.legacyPaths).map(([k,v])=>`<span class="stat">${esc(k.split('_').pop().toLowerCase())} <b>${esc(String(v).split(' ')[0])}</b></span>`).join('')}
 $('clock').textContent='updated '+d.now;
 if(d.control){ctl=d.control;renderCtl()}
 renderChron(d.chronicle);
 if(d.control&&d.control.paused){st.className='pill off';st.textContent='⏸ paused (finishes current turn first)'}
 $('feedTitle').textContent='Current session — '+(d.session.name||'none');
 if(d.session.name!==lastSession){openSet.clear();lastSession=d.session.name}
 $('feed').innerHTML=d.session.feed.map((i,idx)=>i.k=='think'?`<div class="it think">${esc(i.text)}</div>`:i.k=='tool'?`<div class="it tool"><span class="tn">${esc(i.name)}</span> ${esc(i.text)}</div>`:
   i.k=='result'?`<div class="it result${openSet.has(idx)?' open':''}" data-i="${idx}" title="click to expand"><span class="rn">↳ ${esc(i.name)}:</span> ${esc(i.text)}</div>`:`<div class="it done">✔ ${esc(i.text)}${i.cost?` <span class="muted">($${i.cost.toFixed(3)})</span>`:''}</div>`).join('');
 if(stick)$('feed').scrollTop=1e9;
 $('hist').innerHTML=d.history.slice().reverse().map(h=>h.kind=='event'?`<div><span class="muted">${esc(h.time)}</span> <span class="ev">${esc(h.summary)}</span></div>`:
   `<div><span class="t">${h.kind=='review'?'Review T':'T'}${h.turn}</span><span class="muted">${esc(h.time)} · ${esc(h.brain)} · ${h.tools} tools · ${h.secs}s</span><br>${esc(h.summary)}</div>`).join('');
 $('notes').textContent=d.notes;
 $('journal').innerHTML=d.journal.slice().reverse().map(l=>`<div>${esc(l.replace(/^- /,''))}</div>`).join('')||'<span class="muted">empty</span>';
 const L=d.live||{};

 const R=L.rankings;
 if(R&&R.players){
  const rk=(n)=>`<span class="rk ${n==1?'rk1':n==2?'rk2':'rkn'}">#${n}</span>`;
  $('standTitle').textContent=`Standings vs rivals — ${R.known-1} met, ${R.unmetMajors} unmet`;
  const lk=Object.keys(R.legacyMax);
  const ours=R.ourRank||{};
  let h=`<div style="margin-bottom:8px">Our rank among known civs: `+[['Science','sci'],['Culture','cult'],['Gold','gold'],['Happiness','happy'],['Settlements','settlements'],['Legacy','legacyTotal']].map(([n,k])=>`${n} ${rk(ours[k])}`).join(' &nbsp;')+`</div>`;
  h+=`<table><tr><th>Civ</th><th>Sci</th><th>Cult</th><th>Gold</th><th>Happy</th><th>Settl.</th>`+lk.map(k=>`<th>${k.slice(0,4)} /${R.legacyMax[k]}</th>`).join('')+`</tr>`;
  const best={};for(const k of ['sci','cult','gold','happy','settlements'])best[k]=Math.max(...R.players.map(p=>p[k]||0));
  h+=R.players.map(p=>`<tr class="${p.me?'me':''}"><td>${esc(p.name)}${p.war?' <span style="color:var(--bad)">⚔</span>':''}${p.relationship?` <span class="muted">${esc(p.relationship)}</span>`:''}</td>`+
    ['sci','cult','gold','happy','settlements'].map(k=>`<td${p[k]==best[k]&&best[k]>0?' style="color:var(--ok);font-weight:600"':''}>${p[k]??'—'}</td>`).join('')+
    lk.map(k=>`<td>${p.legacy[k]}<div class="bar"><i style="width:${Math.min(100,100*p.legacy[k]/(R.legacyMax[k]||1))}%"></i></div></td>`).join('')+`</tr>`).join('')+`</table>`;
  $('standings').innerHTML=h;
  $('stats').innerHTML+=`<span class="stat">Rank <b>sci ${rk(ours.sci)} cult ${rk(ours.cult)} legacy ${rk(ours.legacyTotal)}</b></span>`;
 } else $('standings').innerHTML='<span class="muted">—</span>';
 $('cities').innerHTML=(L.cities||[]).length?`<table><tr><th>Name</th><th>Pop</th><th>Producing</th><th>Food/Prod/Sci/Gold</th></tr>`+L.cities.map(c=>`<tr><td>${esc(c.name)}${c.capital?' ★':''}${c.town?' <span class="muted">(town)</span>':''}</td><td>${c.pop}</td><td>${esc(c.producing||c.focus||'—')}${c.turnsLeft?` <span class="muted">${c.turnsLeft}t</span>`:''}</td><td>${[c.yields.food,c.yields.prod,c.yields.sci,c.yields.gold].join(' / ')}</td></tr>`).join('')+'</table>':'<span class="muted">—</span>';
 $('units').innerHTML=(L.units||[]).length?`<table><tr><th>Unit</th><th>At</th><th>HP</th><th>Status</th></tr>`+L.units.map(u=>`<tr><td>${esc(u.type.toLowerCase().replace(/_/g,' '))}</td><td>${u.at}</td><td>${u.hp}/${u.maxHp}</td><td>${esc((u.activity||'').toLowerCase())}${u.headingTo?' → '+u.headingTo:''}</td></tr>`).join('')+'</table>':'<span class="muted">—</span>';
 const P=(L.players||[]).filter(p=>p.name);
 $('players').innerHTML=P.length?`<table><tr><th>Name</th><th>Type</th><th>Status</th></tr>`+P.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.kind)}</td><td>${p.war?'<span style="color:var(--bad)">war</span>':''}${p.allied?' allied':''} ${esc((p.relationship||'').toLowerCase())}${p.suzerain?' suzerain: '+esc(p.suzerain):''}</td></tr>`).join('')+'</table>':'<span class="muted">none met yet</span>';
}
tick();setInterval(tick,3000);
let tab='overview';
// ---------- tabs (generalised) ----------
const TABS=['overview','map','charts','tree','civs','costs'];
document.querySelectorAll('.tabs a').forEach(a=>a.addEventListener('click',()=>{tab=a.dataset.tab;document.querySelectorAll('.tabs a').forEach(b=>b.classList.toggle('on',b==a));
 for(const t of TABS)$('tab-'+t).style.display=t==tab?'':'none';if(tab=='civs')civTick();if(tab=='map')mapTick();if(tab=='charts')chartTick();if(tab=='tree')treeTick();if(tab=='costs')costTick();}));

// ---------- controls ----------
let ctl=null;
async function postCtl(o){try{ctl=await (await fetch('/api/control',{method:'POST',body:JSON.stringify(o)})).json();renderCtl()}catch(e){}}
function renderCtl(){if(!ctl)return;const b=$('btnPause');b.textContent=ctl.paused?'▶ Resume':'⏸ Pause';b.classList.toggle('paused',!!ctl.paused);
 $('cFollow').checked=!!ctl.followCamera;renderWho();$('cDelay').value=String(ctl.actionDelay);$('cTurn').value=String(ctl.turnDelay);}
// brain pickers are filled from what this machine actually has (installed CLIs, API keys, Ollama models)
let BRAINS=[],DEFS={};
const brainName=spec=>{if(spec==null||spec==='')return'?';const b=BRAINS.find(b=>b.spec==spec);return b?b.label.split(' (')[0].split(' - ')[0]:String(spec)};
const defLabel=key=>DEFS[key]!=null?`default (${brainName(DEFS[key])})`:'default';
async function loadBrains(){try{BRAINS=await (await fetch('/api/brains')).json()}catch(e){BRAINS=[]}renderWho()}
// read-only summary in the header; the Config & costs tab is the one place to change it
function renderWho(){if(!ctl||!$('whoPlays'))return;
 const eff=k=>{const v=ctl[k]||DEFS[k];return v=='turn'?'same as hard turns':brainName(v)};
 const ev=ctl.modelEvery!==''&&ctl.modelEvery!=null?+ctl.modelEvery:DEFS.modelEvery;
 const items=[['strategist','reviewBrain'],['hard turns','turnBrain'],['routine','routineBrain'],['retry','retryBrain']].map(([n,k])=>`${n} <b style="color:var(--fg)">${esc(eff(k))}</b>`);
 if(ev!=null)items.push(ev?`model check-in every <b style="color:var(--fg)">${ev}</b> turns`:'no forced check-in');
 $('whoPlays').innerHTML=items.join(' · ')+' <span style="color:var(--blue)">✎</span>'}
$('cLearn').onchange=e=>postCtl({learning:e.target.value});
$('cResearch').onchange=e=>postCtl({researchPerAge:+e.target.value});
$('whoPlays').onclick=()=>document.querySelector('.tabs a[data-tab="costs"]').click();
function pickBrain(e,key){let v=e.target.value;if(v=='__custom'){v=(window.prompt('Brain spec, e.g. openai:gpt-5.1, ollama:llama3.3, openrouter:vendor/model, codex:gpt-5.5','')||'').trim();if(!v){renderCtl();return}}
 postCtl({[key]:v}).then(loadBrains)}
setTimeout(loadBrains,300);
$('btnPause').onclick=()=>postCtl({paused:!(ctl&&ctl.paused)});
$('cFollow').onchange=e=>postCtl({followCamera:e.target.checked});
$('cDelay').onchange=e=>postCtl({actionDelay:parseFloat(e.target.value)});
$('cTurn').onchange=e=>postCtl({turnDelay:parseFloat(e.target.value)});
try{$('cTTS').checked=localStorage.getItem('civtts')=='1'}catch(e){}
$('cTTS').onchange=e=>{try{localStorage.setItem('civtts',e.target.checked?'1':'0')}catch(_){};if(!e.target.checked){sayQ.length=0;speechSynthesis.cancel()}};
try{const v=localStorage.getItem('civvoice');if(v)$('cVoice').value=v}catch(e){}
$('cVoice').onchange=e=>{try{localStorage.setItem('civvoice',e.target.value)}catch(_){}};
let latestChron=null;
$('btnTest').onclick=()=>speak(latestChron?((latestChron.headline?latestChron.headline+'. ':'')+latestChron.entry):'Hail, citizens of Rome. The chronicle is ready.',true);

// ---------- chronicle + headline toasts + narration ----------
let lastChron=null;
// neural narration (server-side edge-tts), queued; falls back to the browser voice if the server can't reach the service
const sayQ=[];let saying=false;
function speakBrowser(t){try{const u=new SpeechSynthesisUtterance(t);u.rate=0.98;speechSynthesis.speak(u)}catch(e){}}
function pump(){if(saying||!sayQ.length)return;saying=true;const t=sayQ.shift();
 const a=new Audio('/api/tts?voice='+encodeURIComponent($('cVoice').value)+'&text='+encodeURIComponent(t));
 const done=()=>{saying=false;setTimeout(pump,400)};a.onended=done;a.onerror=()=>{speakBrowser(t);done()};a.play().catch(()=>{speakBrowser(t);done()})}
function speak(t,force){if(!force&&!$('cTTS').checked)return;sayQ.push(t);pump()}
function toast(h,sub){const t=$('toast');t.innerHTML=esc(h)+(sub?`<small>${esc(sub)}</small>`:'');t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),9000)}
function renderChron(list){
 if(!list||!list.length)return;
 $('chron').innerHTML=list.slice().reverse().map(c=>`<div class="e"><span class="t">Turn ${c.turn}</span>${c.headline?`<span class="hl">${esc(c.headline)}</span>`:''}${esc(c.entry)}</div>`).join('');
 const newest=list[list.length-1];latestChron=newest;const key=newest.turn+'|'+newest.entry.slice(0,40);
 if(lastChron!==null&&key!==lastChron){if(newest.headline)toast(newest.headline,'Turn '+newest.turn);speak((newest.headline?newest.headline+'. ':'')+newest.entry)}
 lastChron=key;
}

// ---------- world map (hex canvas) ----------
const TCOL={o:'#173652',w:'#24587a',M:'#8d8d8d',h:'#7a7147',l:'#5f7a44'};
let mapData=null;
async function mapTick(){
 if(tab!='map')return;
 let m;try{m=await (await fetch('/api/map')).json()}catch(e){return}
 if(m.error){$('mapTitle').textContent='World map — game not reachable';return}
 mapData=m;drawMap(m);
}
function drawMap(m){
 const cv=$('mapc'),wrap=cv.parentElement;const W=m.w,H=m.h;
 const size=Math.max(5,Math.min(16,(wrap.clientWidth-20)/(Math.sqrt(3)*(W+0.5))));
 const hw=Math.sqrt(3)*size,vh=1.5*size;
 cv.width=Math.ceil(hw*(W+0.5))+4;cv.height=Math.ceil(vh*(H-1)+2*size)+4;
 const ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);
 const pos=(x,y)=>{const r=H-1-y;return[2+hw*(x+0.5+((y&1)?0.5:0)),2+size+vh*r]};
 const hex=(cx,cy,s)=>{ctx.beginPath();for(let k=0;k<6;k++){const a=Math.PI/180*(60*k-30);ctx.lineTo(cx+s*Math.cos(a),cy+s*Math.sin(a))}ctx.closePath()};
 for(let y=0;y<H;y++)for(let x=0;x<W;x++){
  const i=y*W+x,t=m.terr[i];if(t=='.')continue;const[cx,cy]=pos(x,y);
  hex(cx,cy,size*0.98);ctx.fillStyle=TCOL[t]||'#555';ctx.fill();
  const o=m.owner[i];if(o>=0&&m.players[o]){ctx.globalAlpha=0.55;ctx.fillStyle=m.players[o].color;ctx.fill();ctx.globalAlpha=1}
  if(m.vis[i]=='1'){ctx.globalAlpha=0.35;ctx.fillStyle='#000';ctx.fill();ctx.globalAlpha=1}
 }
 ctx.font=`${Math.max(9,size*0.95)}px system-ui`;ctx.textAlign='center';
 for(const u of m.units){const[cx,cy]=pos(u.x,u.y);const p=m.players[u.owner]||{};ctx.beginPath();ctx.arc(cx,cy,size*0.28,0,7);ctx.fillStyle=p.me?'#ffd400':(p.kind=='independent'?'#ff4d4d':p.color||'#ccc');ctx.fill();ctx.lineWidth=1;ctx.strokeStyle='#000';ctx.stroke()}
 for(const c of m.cities){const[cx,cy]=pos(c.x,c.y);const p=m.players[c.owner]||{};ctx.beginPath();ctx.arc(cx,cy,size*(c.town?0.5:0.65),0,7);ctx.fillStyle=p.color||'#999';ctx.fill();ctx.lineWidth=2;ctx.strokeStyle=p.me?'#ffd400':'#fff';ctx.stroke();
  if(c.capital){ctx.fillStyle='#fff';ctx.fillText('★',cx,cy+size*0.3)}
  const label=(c.name||'')+(c.pop!=null?' '+c.pop:'');ctx.fillStyle='#000';ctx.fillText(label,cx+1,cy-size*0.95+1);ctx.fillStyle=p.me?'#ffe27a':'#eee';ctx.fillText(label,cx,cy-size*0.95)}
 if(m.focus){const[cx,cy]=pos(m.focus.x,m.focus.y);const r=size*(1.2+0.3*Math.sin(Date.now()/300));ctx.beginPath();ctx.arc(cx,cy,r,0,7);ctx.lineWidth=2.5;ctx.strokeStyle='#58a6ff';ctx.stroke()}
 const seen=m.terr.split('').filter(c=>c!='.').length;
 $('mapTitle').textContent=`World map — turn ${m.turn} · ${Math.round(100*seen/(W*H))}% explored · ${m.cities.length} settlements seen`;
 $('mapLegend').innerHTML=Object.entries(m.players).map(([id,p])=>`<span class="chip"><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${p.color};margin-right:5px;vertical-align:-1px"></i>${esc(p.name)}${p.me?' (us)':''}</span>`).join('')+
  '<span class="chip">● yellow = our units</span><span class="chip">● red = independents</span><span class="chip" style="color:#58a6ff">◯ agent\'s last action</span>';
}
setInterval(()=>{if(tab=='map'&&mapData)drawMap(mapData)},120);
window.addEventListener('resize',()=>{if(mapData)drawMap(mapData)});



// ---------- chronicle video ----------
let VINFO=null;
function vRangeFrom(){const r=$('vRange').value;const last=VINFO&&VINFO.entries.length?VINFO.entries[VINFO.entries.length-1][0]:0;return r=='all'?1:Math.max(1,last-(+r)+1)}
function vEstimate(){
 if(!VINFO||!$('vPrem').checked)return '';
 const from=vRangeFrom();const ents=VINFO.entries.filter(e=>e[0]>=from);const ill=$('vIll').value;
 const an=$('vAnim').value;const anims=an=='all'?ents.length:an=='headlines'?ents.filter(e=>e[1]).length:0;
 const imgs=Math.max(anims,ill=='all'?ents.length:ill=='headlines'?ents.filter(e=>e[1]).length:0);const P=VINFO.price;
 const cost=ents.length*P.tts_per_entry+imgs*P.image+anims*(P.veo_clip||0.5)+P.music_track;
 return ` · est. ~$${cost.toFixed(2)} (${ents.length} narrations, ${imgs} images, ${anims} animated scenes, 1 score; cached parts are free)`;
}
async function videoTick(){
 let v;try{v=await (await fetch('/api/video')).json()}catch(e){return}
 VINFO=v;
 if(v.premiumAvailable){$('premWrap').style.display='';if(!$('vGVoice').options.length)$('vGVoice').innerHTML=v.voices.map(x=>`<option>${x}</option>`).join('');
  const on=$('vPrem').checked;for(const id of ['vIll','vGVoice','vAnim','vStyle'])$(id).style.display=on?'':'none'}
 const b=$('btnVideo');b.disabled=v.running;b.textContent=v.running?`🎬 ${v.pct}% …`:'🎬 Generate video';
 let h='';
 if(v.running)h=`Rendering: ${esc(v.msg)} <div class="bar" style="margin-top:3px"><i style="width:${v.pct}%"></i></div>`;
 else if(v.error)h=`<span style="color:var(--bad)">Video failed: ${esc(v.error)}</span>`;
 if(v.videos&&v.videos.length)h+=(h?'<br>':'')+'Videos: '+v.videos.slice(0,4).map(f=>`<a href="/videos/${encodeURIComponent(f)}" target="_blank" style="color:#58a6ff">${esc(f.replace('chronicle_','').replace('.mp4',''))}</a>`).join(' · ');
 if(!v.running&&$('vPrem').checked)h=(h?h+'<br>':'')+'<span style="color:#ffe29a">Premium'+esc(vEstimate())+'</span>';
 $('vStatus').innerHTML=h;
}
$('btnVideo').onclick=async()=>{
 const from=vRangeFrom();
 await fetch('/api/video',{method:'POST',body:JSON.stringify({from,voice:$('cVoice').value,premium:$('vPrem').checked,illustrate:$('vIll').value,geminiVoice:$('vGVoice').value,animate:$('vAnim').value,style:$('vStyle').value})});videoTick();
};
for(const id of ['vPrem','vIll','vRange','vAnim','vStyle'])$(id).addEventListener('change',videoTick);
try{if(localStorage.getItem('civprem')=='1')$('vPrem').checked=true}catch(e){}
$('vPrem').addEventListener('change',e=>{try{localStorage.setItem('civprem',e.target.checked?'1':'0')}catch(_){}});
setInterval(videoTick,2500);videoTick();
// ---------- tech / civic tree ----------
let TREES=null,treeSel=0;
async function treeTick(){
 if(tab!='tree')return;
 let t;try{t=await (await fetch('/api/trees')).json()}catch(e){return}
 if(t.error){$('treeInfo').textContent='game not reachable: '+t.error;return}
 TREES=t;if(treeSel>=t.trees.length)treeSel=0;
 $('treeTabs').innerHTML=t.trees.map((tr,i)=>{const done=tr.nodes.filter(n=>n.state=='fully_unlocked'||n.state=='unlocked').length;
  return `<a class="chip ${i==treeSel?'good':''}" data-i="${i}" style="cursor:pointer">${esc(tr.name)} · ${done}/${tr.nodes.length}</a>`}).join('')+`<span class="muted" style="margin-left:8px">${esc((t.age||'').replace('AGE_','').toLowerCase())} age · turn ${t.turn}</span>`;
 document.querySelectorAll('#treeTabs a').forEach(a=>a.onclick=()=>{treeSel=+a.dataset.i;drawTree()});
 drawTree();
}
const NSTYLE={fully_unlocked:['#12361f','#3fb950','#b9f3c4'],unlocked:['#10302e','#39c5cf','#bdf2f5'],in_progress:['#123046','#58a6ff','#d6e8ff'],open:['#2d2a14','#e3b341','#ffe7a3'],closed:['#1b1f27','#39404e','#8b93a7'],invalid:['#1b1f27','#39404e','#8b93a7']};
function drawTree(){
 const tr=TREES&&TREES.trees[treeSel];if(!tr)return;
 const byKey={};tr.nodes.forEach(n=>byKey[n.key]=n);
 const parents={};tr.nodes.forEach(n=>n.children.forEach(c=>(parents[c]=parents[c]||[]).push(n.key)));
 const cols={};tr.nodes.forEach(n=>(cols[n.depth]=cols[n.depth]||[]).push(n));
 const depths=Object.keys(cols).map(Number).sort((a,b)=>a-b);
 const row={};
 depths.forEach((dp,di)=>{const L=cols[dp];
  L.forEach((n,i)=>{const ps=(parents[n.key]||[]).filter(k=>row[k]!=null);n._b=ps.length?ps.reduce((s,k)=>s+row[k],0)/ps.length:i});
  L.sort((a,b)=>a._b-b._b);let last=-1;L.forEach(n=>{let r=Math.max(Math.round(n._b),last+1);row[n.key]=r;last=r})});
 const NW=168,NH=54,GX=64,GY=16,PAD=14;
 const maxRow=Math.max(...Object.values(row));
 const W=PAD*2+depths.length*NW+(depths.length-1)*GX,H=PAD*2+(maxRow+1)*NH+maxRow*GY;
 const X=n=>PAD+depths.indexOf(n.depth)*(NW+GX),Y=n=>PAD+row[n.key]*(NH+GY);
 let s=`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
 tr.nodes.forEach(n=>n.children.forEach(c=>{const m=byKey[c];if(!m)return;const x1=X(n)+NW,y1=Y(n)+NH/2,x2=X(m),y2=Y(m)+NH/2,mx=(x1+x2)/2;
  const lit=(n.state=='fully_unlocked'||n.state=='unlocked');
  s+=`<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="${lit?'#3fb95099':'#39404e'}" stroke-width="2"/>`}));
 tr.nodes.forEach((n,i)=>{const st=n.active?'in_progress':n.state;const[bg,bd,fg]=NSTYLE[st]||NSTYLE.closed;const x=X(n),y=Y(n);
  s+=`<g class="tn" data-i="${i}" style="cursor:pointer"><rect x="${x}" y="${y}" width="${NW}" height="${NH}" rx="8" fill="${bg}" stroke="${bd}" stroke-width="${n.active?3:1.5}"/>`;
  s+=`<text x="${x+10}" y="${y+20}" fill="${fg}" font-size="13" font-weight="600">${esc(n.name.length>19?n.name.slice(0,18)+'…':n.name)}${n.target?' ★':''}</text>`;
  const sub=n.active?`researching · ${n.turns??'?'} turns`:st=='fully_unlocked'?(n.maxDepth>1?`mastered ${n.depthUnlocked}/${n.maxDepth}`:'researched'):st=='unlocked'?`mastery ${n.depthUnlocked}/${n.maxDepth} · ${n.turns??'?'}t`:st=='open'?`available · ${n.turns??'?'} turns`:'locked';
  s+=`<text x="${x+10}" y="${y+38}" fill="${fg}" opacity=".75" font-size="11">${esc(sub)}</text>`;
  if(n.active&&n.cost){const f=Math.min(1,(n.progress||0)/n.cost);s+=`<rect x="${x+6}" y="${y+NH-7}" width="${(NW-12)*f}" height="3" rx="1.5" fill="#58a6ff"/>`}
  s+='</g>'});
 s+='</svg>';
 const svg=$('treeSvg');svg.outerHTML=s.replace('<svg ','<svg id="treeSvg" ');
 document.querySelectorAll('#treeSvg .tn').forEach(g=>g.onclick=()=>{const n=tr.nodes[+g.dataset.i];
  const by={};(n.unlocks||[]).forEach(u=>(by[u.depth]=by[u.depth]||[]).push(`${u.name} <span class="muted">(${u.kind})</span>`));
  $('treeInfo').innerHTML=`<b style="color:var(--fg)">${esc(n.name)}</b>${n.target?' ★ agent\'s target':''} — ${esc(n.active?'researching now':n.state.replace('_',' '))} · cost ${n.cost??'?'}${n.turns!=null?` · ${n.turns} turns`:''}${n.maxDepth>1?` · mastery ${n.depthUnlocked}/${n.maxDepth}`:''}<br>`+
   (Object.keys(by).length?Object.entries(by).map(([dp,l])=>`<span class="muted">${n.maxDepth>1?(dp==1?'Unlocks':'Mastery unlocks'):'Unlocks'}:</span> ${l.join(', ')}`).join('<br>'):'<span class="muted">No listed unlocks (passive bonus).</span>')});
}
setInterval(()=>{if(tab=='tree')treeTick()},10000);
// ---------- charts (zoomable: range buttons, drag-select, wheel, dbl-click reset, hover readout) ----------
let H=[],CH={ids:[],nm:{},col:{}},zoom=null; // zoom = [t0,t1] or null for all
const METRICS=[['Science per turn','sci'],['Culture per turn','cult'],['Gold in treasury','gold'],['Technologies researched','techs'],['Settlements founded (total)','cities'],['Buildings constructed (total)','buildings'],['Enemy units killed (total)','kills'],['Happiness per turn (recorded from T68)','happy'],['Legacy points this age (recorded from T68)','legacyTotal']];
const PL=46,PR=600,PT=16,PB=190;
async function chartTick(){
 if(tab!='charts')return;
 let h,m;try{h=await (await fetch('/api/history')).json();m=mapData||await (await fetch('/api/map')).json()}catch(e){return}
 if(!h.length)return;
 H=h;
 const colors={};if(m&&m.players)for(const[id,p]of Object.entries(m.players))colors[id]=p.color;
 const pal=['#58a6ff','#f0883e','#3fb950','#bc8cff','#ff7b72','#d29922','#39c5cf'];
 CH.ids=[...new Set(h.flatMap(r=>r.players.map(p=>p.id)))];
 h.forEach(r=>r.players.forEach(p=>CH.nm[p.id]=p.name));
 CH.me=new Set(h.flatMap(r=>r.players.filter(p=>p.me).map(p=>p.id)));
 CH.ids.forEach((id,i)=>{const c=colors[id];CH.col[id]=CH.me.has(id)?'#ffd400':(c&&c!='rgb(51, 51, 51)'&&c!='rgb(0, 0, 0)')?c:pal[i%pal.length]});
 if(!$('chartBar'))buildCharts();
 drawCharts();
}
function span(){const all=H.map(r=>r.turn);const a=Math.min(...all),b=Math.max(...all);if(!zoom)return[a,Math.max(b,a+1)];return[Math.max(a,zoom[0]),Math.min(b,Math.max(zoom[1],zoom[0]+1))]}
function buildCharts(){
 $('charts').innerHTML=`<div id="chartBar" class="chips" style="grid-column:1/-1;align-items:center">
  <span class="muted">Range:</span>${[['10','Last 10'],['25','Last 25'],['50','Last 50'],['all','All']].map(([v,n])=>`<a class="chip rng" data-r="${v}" style="cursor:pointer">${n}</a>`).join('')}
  <span class="muted" id="rngLbl"></span><span class="muted" style="margin-left:auto">drag to zoom · wheel to zoom · double-click to reset · hover for values</span></div>`+
  METRICS.map(([t,k])=>`<section class="chart"><h2>${t}</h2><svg id="ch_${k}" data-k="${k}" viewBox="0 0 610 210" style="cursor:crosshair;touch-action:none"></svg><div class="lg" id="lg_${k}"></div></section>`).join('');
 document.querySelectorAll('.rng').forEach(a=>a.onclick=()=>{const all=H.map(r=>r.turn),b=Math.max(...all);zoom=a.dataset.r=='all'?null:[b-(+a.dataset.r),b];drawCharts()});
 for(const[,k]of METRICS){const svg=$('ch_'+k);let drag=null;
  const tAt=e=>{const r=svg.getBoundingClientRect();const x=(e.clientX-r.left)*610/r.width;const[a,b]=span();return a+(b-a)*Math.min(1,Math.max(0,(x-PL)/(PR-PL)))};
  svg.onmousedown=e=>{drag={t:tAt(e)};e.preventDefault()};
  svg.onmousemove=e=>{const t=tAt(e);if(drag){drag.cur=t;drawCharts(null,[Math.min(drag.t,t),Math.max(drag.t,t)])}else drawCharts(t)};
  svg.onmouseleave=()=>{if(!drag)drawCharts()};
  window.addEventListener('mouseup',e=>{if(!drag)return;const a=Math.min(drag.t,drag.cur??drag.t),b=Math.max(drag.t,drag.cur??drag.t);drag=null;if(b-a>=2)zoom=[Math.floor(a),Math.ceil(b)];drawCharts()});
  svg.ondblclick=()=>{zoom=null;drawCharts()};
  svg.onwheel=e=>{e.preventDefault();const t=tAt(e);const[a,b]=span();const f=e.deltaY<0?0.75:1.35;let na=t-(t-a)*f,nb=t+(b-t)*f;const all=H.map(r=>r.turn);
   if(nb-na<3){na=t-1.5;nb=t+1.5}if(na<=Math.min(...all)&&nb>=Math.max(...all))zoom=null;else zoom=[na,nb];drawCharts(t)};
 }
}
function drawCharts(hoverT,sel){
 if(!H.length)return;
 const[t0,t1]=span();const rows=H.filter(r=>r.turn>=t0-0.001&&r.turn<=t1+0.001);
 $('rngLbl').textContent=`showing T${Math.round(t0)}–T${Math.round(t1)}${zoom?'':' (all)'}`;
 const hv=hoverT!=null?rows.reduce((best,r)=>best==null||Math.abs(r.turn-hoverT)<Math.abs(best.turn-hoverT)?r:best,null):null;
 for(const[,k]of METRICS){
  const vals=rows.flatMap(r=>r.players.map(p=>p[k]).filter(v=>v!=null));if(!vals.length){$('ch_'+k).innerHTML='<text x="305" y="105" fill="#8b93a7" text-anchor="middle" font-size="13">no data in this range yet</text>';continue}let vmax=Math.max(1,...vals),vmin=Math.min(0,...vals);const pad=(vmax-vmin)*0.06;vmax+=pad;
  const X=t=>PL+(PR-PL)*(t-t0)/(t1-t0),Y=v=>PB-(PB-PT)*(v-vmin)/(vmax-vmin);
  let s=`<rect x="0" y="0" width="610" height="210" fill="transparent"/>`;
  for(let g=0;g<=4;g++){const v=vmin+(vmax-vmin)*g/4;s+=`<line x1="${PL}" y1="${Y(v)}" x2="${PR}" y2="${Y(v)}" stroke="#1f2430"/><text x="${PL-4}" y="${Y(v)+3}" fill="#8b93a7" font-size="10" text-anchor="end">${Math.round(v*10)/10}</text>`}
  const step=Math.max(1,Math.ceil((t1-t0)/8));for(let t=Math.ceil(t0);t<=t1;t+=step)s+=`<text x="${X(t)}" y="204" fill="#8b93a7" font-size="10" text-anchor="middle">T${t}</text>`;
  if(sel)s+=`<rect x="${X(sel[0])}" y="${PT}" width="${Math.max(1,X(sel[1])-X(sel[0]))}" height="${PB-PT}" fill="#58a6ff22" stroke="#58a6ff66"/>`;
  s+=`<clipPath id="cp_${k}"><rect x="${PL}" y="0" width="${PR-PL}" height="${PB+2}"/></clipPath><g clip-path="url(#cp_${k})">`;
  CH.ids.forEach(id=>{const pts=rows.map(r=>{const p=r.players.find(p=>p.id==id);return p&&p[k]!=null?[X(r.turn),Y(p[k])]:null}).filter(Boolean);
   if(!pts.length)return;const me=CH.me.has(id);
   s+=`<polyline fill="none" stroke="${CH.col[id]}" stroke-width="${me?3:1.8}" stroke-linejoin="round" points="${pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')}"/>`;
   if(pts.length<=30)s+=pts.map(p=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${me?2.6:2}" fill="${CH.col[id]}"/>`).join('')});
  s+='</g>';
  let lg=CH.ids.map(id=>`<span><i style="background:${CH.col[id]}"></i>${esc(CH.nm[id]||id)}</span>`).join('');
  if(hv){const x=X(hv.turn);s+=`<line x1="${x}" y1="${PT}" x2="${x}" y2="${PB}" stroke="#ffffff55" stroke-dasharray="3,3"/>`;
   const ps=hv.players.filter(p=>p[k]!=null).sort((a,b)=>b[k]-a[k]);
   const bx=x>PR-170?x-172:x+8;s+=`<rect x="${bx}" y="${PT}" width="164" height="${16+13*ps.length}" rx="6" fill="#0f1115ee" stroke="#333a48"/><text x="${bx+8}" y="${PT+13}" fill="#e3b341" font-size="11" font-weight="600">Turn ${hv.turn}</text>`+
    ps.map((p,i)=>`<circle cx="${bx+11}" cy="${PT+23+13*i}" r="3.5" fill="${CH.col[p.id]}"/><text x="${bx+20}" y="${PT+27+13*i}" fill="${p.me?'#ffe27a':'#d8dce6'}" font-size="10.5">${esc((CH.nm[p.id]||'').split(' (')[0].split(',')[0].slice(0,16))}: ${Math.round((p[k]||0)*10)/10}</text>`).join('');
   ps.forEach(p=>{s+=`<circle cx="${x}" cy="${Y(p[k])}" r="4" fill="none" stroke="${CH.col[p.id]}" stroke-width="2"/>`})}
  $('ch_'+k).innerHTML=s;$('lg_'+k).innerHTML=lg;
 }
}
setInterval(()=>{if(tab=='map')mapTick();if(tab=='charts')chartTick();if(tab=='costs')costTick()},8000);

// ---------- config & costs ----------
const ROLES=[['reviewBrain','Strategist','strategy review every 10 turns and at each new age'],
 ['turnBrain','Hard turns','turns with threats, diplomacy, tech/civic/policy choices, settlers, events'],
 ['routineBrain','Routine turns','production, growth, promotions only. Jev picks from the options and rules move units'],
 ['retryBrain','Retry','second try when a turn was not ended']];
const ROLECOL={review:'#bc8cff',turn:'#58a6ff',retry:'#f0883e',jev:'#3fb950',research:'#e3b341'};
const ROLENAME={review:'strategist',turn:'model turn',retry:'retry',jev:'Jev turn',research:'research'};
function cfgOptions(key){const cur=(ctl&&ctl[key])||'';let o=[['',defLabel(key)]];
 if(key=='routineBrain')o.push(['turn','same as hard turns']);
 o=o.concat(BRAINS.filter(b=>key=='routineBrain'||b.provider!='jev').map(b=>[b.spec,b.label]));
 if(cur&&!o.some(x=>x[0]==cur))o.push([cur,cur+' (custom)']);o.push(['__custom','custom…']);
 return o.map(([v,n])=>`<option value="${esc(v)}"${v==cur?' selected':''}>${esc(n)}</option>`).join('')}
function renderCfg(){if(!ctl||!$('cfgTbl'))return;
 if(document.activeElement!==$('cLearn'))$('cLearn').value=ctl.learning||'online';
 if(document.activeElement!==$('cResearch'))$('cResearch').value=String(ctl.researchPerAge||3);if($('cfgTbl').contains(document.activeElement))return;
 $('cfgTbl').innerHTML=ROLES.map(([k,n,d])=>`<tr><th style="width:120px">${n}</th><td><select data-k="${k}" class="cfgSel">${cfgOptions(k)}</select><div class="muted" style="font-size:11.5px">${d}</div></td></tr>`).join('')+
  `<tr><th>Model check-in</th><td><select id="cfgEvery">${[['',`default (${DEFS.modelEvery!=null?(+DEFS.modelEvery?'every '+DEFS.modelEvery+' turns':'never force'):'3'})`],['0','never force'],['2','every 2 turns'],['3','every 3 turns'],['5','every 5 turns'],['10','every 10 turns']].map(([v,n])=>`<option value="${v}"${String(ctl.modelEvery??'')==v?' selected':''}>${n}</option>`).join('')}</select><div class="muted" style="font-size:11.5px">a model plays at least this often, even when every turn is routine</div></td></tr>`;
 document.querySelectorAll('.cfgSel').forEach(s=>s.onchange=e=>pickBrain(e,s.dataset.k));
 $('cfgEvery').onchange=e=>postCtl({modelEvery:e.target.value});}
const _renderCtl=renderCtl;renderCtl=function(){_renderCtl();renderCfg()};
const _loadBrains=loadBrains;loadBrains=async function(){await _loadBrains();renderCfg()};
const usd=v=>v==null?'–':v<0.01&&v>0?'$'+v.toFixed(4):'$'+v.toFixed(3);
async function skillTick(){
 let k;try{k=await (await fetch('/api/skills')).json()}catch(e){return}
 $('skillList').innerHTML='Skills: '+(k.skills.map(x=>`<b style="color:var(--fg)">${esc(x.folder)}</b> <span title="${esc(x.description)}">(${Math.round(x.chars/1000*10)/10}k chars)</span>`).join(' · ')||'none');
 $('lessons').textContent=(k.lessons||'(no lessons saved yet: the agent adds them with remember_lesson as it plays)').replace(/^---[\s\S]*?---\s*/,'');
 const age=(window.lastAge||''),used=k.researchUsage[age]||0;$('researchUsed').textContent=`used ${used} this age · each research session costs about $0.10-0.50`}
async function costTick(){
 if(tab!='costs')return;renderCfg();skillTick();
 let c;try{c=await (await fetch('/api/costs')).json()}catch(e){return}
 const s=c.summary;
 $('costTiles').innerHTML=[['last 10 turns',s.last10],['last 25',s.last25],['this age',s.thisAge],['whole game',s.all]].map(([n,v])=>`<div><div class="muted" style="font-size:11.5px">${n}</div><b style="font-size:20px">${usd(v)}</b><span class="muted"> /turn</span></div>`).join('')+
  `<div><div class="muted" style="font-size:11.5px">total spent</div><b style="font-size:20px">$${s.total.toFixed(2)}</b></div>`;
 const bb=Object.entries(c.byBrain).sort((a,b)=>b[1].cost-a[1].cost);
 $('byBrain').innerHTML='<table><tr><th>role · model</th><th>sessions</th><th>total</th><th>avg</th></tr>'+bb.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.sessions}</td><td>$${v.cost.toFixed(2)}</td><td>${usd(v.cost/v.sessions)}</td></tr>`).join('')+'</table>';
 const T=c.turns.slice(-80);if(!T.length)return;
 const max=Math.max(0.05,...T.map(t=>t.total))*1.08,L=46,R=990,Tp=10,B=210,w=(R-L)/T.length;
 let g='';for(let i=0;i<=4;i++){const v=max*i/4,y=B-(B-Tp)*i/4;g+=`<line x1="${L}" x2="${R}" y1="${y}" y2="${y}" stroke="#1f2430"/><text x="${L-4}" y="${y+3}" fill="#8b93a7" font-size="10" text-anchor="end">$${v.toFixed(2)}</text>`}
 T.forEach((t,i)=>{let y=B;const x=L+i*w;
  if(i&&t.seg!=T[i-1].seg)g+=`<line x1="${x}" x2="${x}" y1="${Tp}" y2="${B}" stroke="#e3b341" stroke-dasharray="4,3"/><text x="${x+3}" y="${Tp+10}" fill="#e3b341" font-size="10">new age</text>`;
  for(const p of t.parts){const h=(B-Tp)*p.cost/max;y-=h;g+=`<rect x="${x+1}" y="${y}" width="${Math.max(1,w-2)}" height="${Math.max(h,p.cost>0?1:0)}" fill="${ROLECOL[p.role]}"><title>T${t.turn} ${ROLENAME[p.role]} · ${p.brain}: ${usd(p.cost)}</title></rect>`}
  if(t.parts.every(p=>p.role=='jev'))g+=`<circle cx="${x+w/2}" cy="${B-3}" r="2.5" fill="${ROLECOL.jev}"><title>T${t.turn}: Jev turn ${usd(t.total)}</title></circle>`;
  if(i%Math.ceil(T.length/16)==0)g+=`<text x="${x+w/2}" y="${B+14}" fill="#8b93a7" font-size="10" text-anchor="middle">T${t.turn}</text>`});
 $('costSvg').innerHTML=g;$('costLbl').textContent=`last ${T.length} turns · hover a bar for details`;
 $('costLg').innerHTML=Object.entries(ROLENAME).map(([k,n])=>`<span><i style="display:inline-block;width:10px;height:10px;background:${ROLECOL[k]};margin-right:4px;vertical-align:-1px"></i>${n}</span>`).join('');
 $('costTbl').innerHTML='<tr><th>turn</th><th>when</th><th>who played</th><th>cost</th></tr>'+c.turns.slice(-60).reverse().map(t=>`<tr><td>T${t.turn}</td><td class="muted">${t.date.slice(5)}</td><td>${t.parts.map(p=>`<span style="color:${ROLECOL[p.role]}">${ROLENAME[p.role]}</span> ${esc(p.brain)} ${usd(p.cost)}${p.handoff?' <span class="muted">(handed to model)</span>':''}`).join(' · ')}</td><td><b>${usd(t.total)}</b></td></tr>`).join('');
}

const cmp=(ours,theirs)=>theirs==null||ours==null?'':theirs>ours?'<span class="neg">▲</span>':theirs<ours?'<span class="pos">▼</span>':'=';
async function civTick(){
 if(tab!='civs')return;
 let d;try{d=await (await fetch('/api/intel')).json()}catch(e){return}
 if(d.error){$('civs').innerHTML=`<span class="muted">game not reachable: ${esc(d.error)}</span>`;return}
 const me=(d.rankings&&d.rankings.players||[]).find(p=>p.me)||{};
 const maxes=d.rankings?d.rankings.legacyMax:{};
 const card=c=>{
  const chips=[];
  if(c.atWarWithUs)chips.push('<span class="chip bad">⚔ at war with us</span>');
  if(c.alliedWithUs)chips.push('<span class="chip good">allied with us</span>');
  if(c.relationship)chips.push(`<span class="chip ${/friend|help/.test(c.relationship)?'good':/hostile|unfriend/.test(c.relationship)?'bad':'warn'}">${esc(c.relationship)}${c.relationshipScore!=null?' ('+c.relationshipScore+')':''}</span>`);
  if(c.government)chips.push(`<span class="chip">${esc(c.government)}</span>`);
  if(c.kind!='major')chips.push(`<span class="chip">${esc(c.kind)}${c.cityStateType?': '+esc(c.cityStateType):''}</span>`);
  if(c.suzerain!==undefined)chips.push(`<span class="chip ${c.suzerain=='us'?'good':''}">suzerain: ${esc(c.suzerain||'none')}</span>`);
  (c.atWarWith||[]).forEach(w=>chips.push(`<span class="chip bad">at war with ${esc(w)}</span>`));
  (c.alliedWith||[]).forEach(w=>chips.push(`<span class="chip">allied with ${esc(w)}</span>`));
  let h=`<section class="civ"><h3>${esc(c.name)}</h3><div class="sub">${esc(c.civ||'')}</div><div class="chips">${chips.join('')}</div>`;
  if(c.yields){h+=`<h4>Output vs us <span style="text-transform:none">(▲ = they're ahead)</span></h4><div class="kv">`+[['Science','sci'],['Culture','cult'],['Gold','gold'],['Happiness','happy'],['Influence','infl']].map(([n,k])=>`<b>${n}</b><span>${c.yields[k]} ${cmp(me[k],c.yields[k])} <span class="muted">us ${me[k]??'—'}</span></span>`).join('')+
    `<b>Settlements</b><span>${c.settlements??'—'} ${cmp(me.settlements,c.settlements)} <span class="muted">us ${me.settlements??'—'}</span></span></div>`}
  if(c.legacy){h+=`<h4>Legacy paths this age</h4><div class="kv">`+Object.entries(c.legacy).map(([k,v])=>`<b>${esc(k)}</b><span>${v}/${maxes[k]??'?'} ${cmp((me.legacy||{})[k],v)} <span class="muted">us ${(me.legacy||{})[k]??0}</span></span>`).join('')+`</div>`}
  if(c.relationshipHistory&&c.relationshipHistory.length){h+=`<h4>Why they feel this way</h4><div class="kv">`+c.relationshipHistory.map(r=>`<b class="${r.total>=0?'pos':'neg'}">${r.total>0?'+':''}${r.total}</b><span>${esc(r.reason)}${r.times>1?` <span class="muted">×${r.times}</span>`:''} <span class="muted">(last T${r.lastTurn})</span></span>`).join('')+`</div>`}
  if(c.sharedActions&&c.sharedActions.length){h+=`<h4>Diplomatic actions between us</h4><div class="kv">`+c.sharedActions.map(a=>`<b>${esc(a.by)}</b><span>${esc(a.action)} <span class="muted">${esc(a.progress)}</span></span>`).join('')+`</div>`}
  h+=`<h4>Settlements we've seen (${c.totalSettlementsSeen}${c.settlements!=null?' of '+c.settlements:''})</h4>`+(c.knownSettlements.length?`<div class="kv">`+c.knownSettlements.map(s=>`<b>${esc(s.name)}${s.capital?' ★':''}</b><span>at ${s.at}${s.pop!=null?' · pop '+s.pop:''}${s.town?' · town':''} <span class="muted">${s.distance!=null?s.distance+' tiles from our capital':''}</span></span>`).join('')+`</div>`:'<span class="muted">none revealed yet</span>');
  const vu=Object.entries(c.visibleUnits||{});
  h+=`<h4>Units in sight now</h4>`+(vu.length?vu.map(([t,n])=>`<span class="chip">${n}× ${esc(t)}</span>`).join(' '):'<span class="muted">none</span>');
  return h+'</section>';
 };
 $('civs').innerHTML=(d.civs||[]).length?d.civs.map(card).join(''):'<span class="muted">No civilizations met yet.</span>';
}
setInterval(civTick,6000);

</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(api_state(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/tts"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            try:
                import media
                with open(media.tts_mp3((q.get("text") or [""])[0][:1500], (q.get("voice") or [""])[0]), "rb") as f:
                    body = f.read()
                ctype = "audio/mpeg"
            except Exception as e:
                self.send_response(502); self.end_headers(); self.wfile.write(str(e).encode()); return
        elif self.path.startswith("/api/video"):
            import premium
            import video as _video
            ents = _video.read_chronicle()
            body = json.dumps({**VIDEO_JOB, "videos": videos(), "premiumAvailable": premium.available(),
                               "voices": premium.GEMINI_VOICES, "price": premium.PRICE,
                               "entries": [[e.get("turn"), bool(e.get("headline"))] for e in ents]}).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/videos/"):
            name = os.path.basename(self.path.split("?")[0])
            path = os.path.join(STATE, "videos", name)
            if not (name.endswith(".mp4") and os.path.exists(path)):
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return
        elif self.path.startswith("/api/trees"):
            body = json.dumps(trees(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/brains"):
            body = json.dumps(brain_list(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/skills"):
            body = json.dumps(skills_report(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/costs"):
            body = json.dumps(cost_report(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/map"):
            body = json.dumps(world_map(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/history"):
            body = json.dumps(full_history(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        elif self.path.startswith("/api/intel"):
            body = json.dumps(intel(), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        else:
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _do_post(self):
    if self.path.startswith("/api/video"):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            opts = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            opts = {}
        out = json.dumps(start_video(opts)).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
        return
    if not self.path.startswith("/api/control"):
        self.send_response(404); self.end_headers(); return
    n = int(self.headers.get("Content-Length") or 0)
    try:
        body = json.loads(self.rfile.read(n) or b"{}")
    except ValueError:
        body = {}
    out = json.dumps(control.update(**body)).encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(out)


H.do_POST = _do_post


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Civ VII autopilot dashboard: http://localhost:{PORT}")
    srv.serve_forever()
