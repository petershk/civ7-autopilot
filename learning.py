"""Learning for the Civ VII agent: lessons it keeps across games, and optional online research.

Lessons live in skills/learned/SKILL.md, which autopilot.py adds to every session's instructions, so
anything remembered here shapes all future turns and games. The strategist review curates the file.
Online research runs a short separate `claude -p` session limited to web search/fetch, capped per age
(dashboard setting); its cost is logged like any other session so it shows on the cost chart.
"""
import datetime as dt
import json
import os
import re
import subprocess
import time

import control

HERE = os.path.dirname(os.path.abspath(__file__))
LESSONS = os.path.join(HERE, "skills", "learned", "SKILL.md")
LOG = os.path.join(HERE, "logs", "autopilot.log")
USAGE = os.path.join(HERE, "state", "research_usage.json")
LEDGER = os.path.join(HERE, "state", "lessons_ledger.jsonl")  # every change to the lessons, with who/when/why
HISTORY = os.path.join(HERE, "state", "lessons_history")       # the file as it was before each rewrite
STATUS = os.path.join(HERE, "state", "status.json")
MAX_CHARS = 9000  # beyond this the lessons eat too much of every prompt; the review should consolidate

HEADER = """---
name: civ7-learned
description: Lessons the Civ VII agent learned from its own games and research (curated at strategy reviews)
---
# Lessons we learned (from our own games and research)
Apply these; they came from real outcomes. Newer lessons override older ones that conflict.
Tags: [verified] = checked against the game's rules data or confirmed by the user. [strategy] = a judgment call
that data can't settle. [unverified] = from a single observation or guess: treat it as a hint and check it
(lookup_rules) before relying on it.
"""
TAGS = ("verified", "strategy", "unverified")
TAG_RE = re.compile(r"\s*\[(verified|strategy|unverified)\]")
PROV_RE = re.compile(r"\s*_\(([^)]*)\)_\s*$")


def _session():
    """Which session is acting (the autopilot records it before each run)."""
    try:
        st = json.load(open(STATUS, encoding="utf-8"))
        return {"session": st.get("session"), "brain": st.get("sessionBrain"), "turn": st.get("turn"), "age": st.get("age")}
    except (OSError, ValueError):
        return {}


def ledger(action, **kw):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "action": action, **_session(), **kw}
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_ledger(n=300):
    try:
        return [json.loads(l) for l in open(LEDGER, encoding="utf-8").read().splitlines()[-n:] if l.strip()]
    except (OSError, ValueError):
        return []


def _plain(line):
    """A lesson without its tag and provenance, for recognising it across rewrites."""
    return " ".join(PROV_RE.sub("", TAG_RE.sub("", line)).lstrip("- ").split()).lower()


def _tag_of(line):
    m = TAG_RE.search(line)
    return m.group(1) if m else None


def _with_tag(line, tag):
    line = TAG_RE.sub("", line).rstrip()
    m = PROV_RE.search(line)
    return (line[:m.start()] + f" [{tag}]" + line[m.start():]) if m else f"{line} [{tag}]"


def bullets(text=None):
    """[(topic, line)] for every lesson."""
    out, topic = [], "General"
    for line in (read_lessons() if text is None else text).splitlines():
        if line.startswith("## "):
            topic = line[3:].strip()
        elif line.startswith("- "):
            out.append((topic, line))
    return out


def lessons_report():
    """Every lesson with its status, topic and where it came from (the dashboard's audit view)."""
    out = []
    for topic, line in bullets():
        prov = PROV_RE.search(line)
        out.append({"topic": topic, "text": PROV_RE.sub("", TAG_RE.sub("", line))[2:].strip(),
                    "status": _tag_of(line) or "unverified", "source": prov.group(1) if prov else ""})
    return out


def unverified():
    return [l for _, l in bullets() if (_tag_of(l) or "unverified") == "unverified"]


def mode():
    """off | ingame | online"""
    return (control.get().get("learning") or "online").lower()


def read_lessons():
    try:
        return open(LESSONS, encoding="utf-8").read()
    except FileNotFoundError:
        return HEADER


def _write(text):
    os.makedirs(os.path.dirname(LESSONS), exist_ok=True)
    with open(LESSONS, "w", encoding="utf-8") as f:
        f.write(text)


def remember(topic, lesson, where=""):
    """Add one lesson under a topic heading (created if new). Skips exact duplicates."""
    if mode() == "off":
        return "learning is turned off in the settings"
    lesson = " ".join(str(lesson).split())
    topic = " ".join(str(topic).split()).strip("# ").title()[:60] or "General"
    if len(lesson) < 12:
        return "lesson too short: write a general, reusable rule (1-2 sentences)"
    text = read_lessons()
    if lesson.lower()[:80] in text.lower():
        return "already known"
    bullet = f"- {lesson} [unverified]" + (f" _({where})_" if where else "")
    head = f"\n## {topic}\n"
    if head in text:
        i = text.index(head) + len(head)
        j = text.find("\n## ", i)
        j = len(text) if j == -1 else j
        text = text[:j].rstrip("\n") + "\n" + bullet + "\n" + text[j:].lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n" + head + bullet + "\n"
    _write(text)
    ledger("add", topic=topic, lesson=lesson, where=where)
    note = "saved as [unverified]; it is checked at the next strategy review"
    if len(text) > MAX_CHARS:
        note += f" (lessons file is {len(text)} chars; the next strategy review should consolidate it)"
    return note


def rewrite(content):
    """Replace the lessons (strategist review curation). Keeps the header."""
    if mode() == "off":
        return "learning is turned off in the settings"
    old = read_lessons()
    body = content.split("---", 2)[-1] if content.lstrip().startswith("---") else content
    body = "\n" + body.strip()
    body = body[body.index("\n## "):] if "\n## " in body else body  # drop any header the model copied
    # a rewrite may merge and reword, but it can't mark anything checked: a lesson keeps [verified]/[strategy]
    # only if that exact lesson already had it; anything new or reworded is [unverified] until the audit
    known = {_plain(l): _tag_of(l) for _, l in bullets(old)}
    lines = []
    for line in body.strip().splitlines():
        if line.startswith("- "):
            tag = known.get(_plain(line))
            line = _with_tag(line, tag if tag in ("verified", "strategy") else "unverified")
        lines.append(line)
    new = HEADER + "\n".join(lines).strip() + "\n"
    os.makedirs(HISTORY, exist_ok=True)
    with open(os.path.join(HISTORY, f"SKILL.{dt.datetime.now():%Y%m%d-%H%M%S}.md"), "w", encoding="utf-8") as f:
        f.write(old)
    _write(new)
    was = {_plain(l): l[2:] for _, l in bullets(old)}
    now = {_plain(l): l[2:] for _, l in bullets(new)}
    ledger("rewrite", removed=[v for k, v in was.items() if k not in now], added=[v for k, v in now.items() if k not in was])
    return f"saved ({len(new)} chars)"


def set_status(fragment, status, evidence="", by=None):
    """Mark one lesson (found by a unique fragment of its text) verified / strategy / unverified, or 'refuted' to
    remove it. The evidence goes in the ledger. by: who decided (default: the current session)."""
    status = str(status).lower().strip()
    if status not in TAGS + ("refuted",):
        return f"status must be one of {TAGS + ('refuted',)}"
    frag = " ".join(str(fragment).split()).lower()
    text = read_lessons()
    hits = [l for _, l in bullets(text) if frag and frag in " ".join(l.split()).lower()]
    if len(hits) != 1:
        return f"{'no' if not hits else len(hits)} lessons match that text; quote a longer, unique part of one lesson"
    line = hits[0]
    lines = text.splitlines()
    i = lines.index(line)
    if status == "refuted":
        del lines[i]
        if lines[i - 1].startswith("## ") and (i >= len(lines) or not lines[i].startswith("- ")):
            del lines[i - 1]  # its topic has no lessons left
    else:
        lines[i] = _with_tag(line, status)
    _write("\n".join(lines).rstrip("\n") + "\n")
    ledger(status, lesson=line[2:], evidence=evidence, **({"by": by} if by else {}))
    return f"{status}: {PROV_RE.sub('', TAG_RE.sub('', line))[2:][:120]}"


def _usage():
    try:
        return json.load(open(USAGE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def research(question, age, turn):
    """Short web-research session. Returns the answer text (with sources) or why it didn't run."""
    if mode() != "online":
        return "online research is turned off in the settings (lookup_rules and lessons still work)"
    c = control.get()
    budget = int(c.get("researchPerAge") or 3)
    use = _usage()
    used = int(use.get(age or "?", 0))
    if used >= budget:
        return f"research budget used up for this age ({used}/{budget}); rely on lessons, skills and lookup_rules"
    prompt = ("Research this question about the video game Sid Meier's Civilization VII (2025; ages Antiquity, "
              "Exploration, Modern) using web search, and answer for an AI agent that is playing right now:\n\n"
              f"{question}\n\nGive concrete, rule-level guidance (numbers, conditions, priorities) in at most 250 words. "
              "Say when sources disagree or you are unsure. Prefer Civ VII sources; ignore Civ VI advice unless it clearly "
              "still applies. End with a 'Sources:' line listing the URLs you used.")
    model = (c.get("researchBrain") or "sonnet").split(":")[-1]
    t0 = time.time()
    try:
        p = subprocess.run(["claude", "-p", prompt, "--model", model, "--tools", "WebSearch,WebFetch",
                            "--allowedTools", "WebSearch,WebFetch", "--setting-sources", "project,local",
                            "--no-session-persistence", "--output-format", "json", "--max-turns", "12"],
                           capture_output=True, text=True, timeout=400, stdin=subprocess.DEVNULL, encoding="utf-8",
                           cwd=HERE)
        data = json.loads(p.stdout or "{}")
        answer, cost = (data.get("result") or "").strip(), data.get("total_cost_usd")
    except Exception as e:
        answer, cost = "", None
        err = str(e)
    else:
        err = "" if answer else (p.stderr or "no answer")[:300]
    use[age or "?"] = used + 1
    os.makedirs(os.path.dirname(USAGE), exist_ok=True)
    json.dump(use, open(USAGE, "w", encoding="utf-8"))
    # same line shape as the model sessions, so the dashboard counts it in history and costs
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] research_T{int(turn or 0):03d}: rc={'ok' if answer else 'error'} "
                f"tools=0 {time.time() - t0:.0f}s cost={cost} brain=claude:{model} :: {question[:120]!r} -> "
                f"{(answer or err)[:200]!r}\n")
    if not answer:
        return f"research failed: {err}"
    return answer + f"\n\n(research {used + 1}/{budget} this age. If this is worth keeping, save it with remember_lesson.)"
