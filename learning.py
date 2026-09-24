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
MAX_CHARS = 9000  # beyond this the lessons eat too much of every prompt; the review should consolidate

HEADER = """---
name: civ7-learned
description: Lessons the Civ VII agent learned from its own games and research (curated at strategy reviews)
---
# Lessons we learned (from our own games and research)
Apply these; they came from real outcomes. Newer lessons override older ones that conflict.
"""


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
    bullet = f"- {lesson}" + (f" _({where})_" if where else "")
    head = f"\n## {topic}\n"
    if head in text:
        i = text.index(head) + len(head)
        j = text.find("\n## ", i)
        j = len(text) if j == -1 else j
        text = text[:j].rstrip("\n") + "\n" + bullet + "\n" + text[j:].lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n" + head + bullet + "\n"
    _write(text)
    note = "saved"
    if len(text) > MAX_CHARS:
        note += f" (lessons file is {len(text)} chars; the next strategy review should consolidate it)"
    return note


def rewrite(content):
    """Replace the lessons (strategist review curation). Keeps the header."""
    if mode() == "off":
        return "learning is turned off in the settings"
    body = content.split("---", 2)[-1] if content.lstrip().startswith("---") else content
    body = re.sub(r"^# Lessons we learned.*?\n(Apply these.*?\n)?", "", body.lstrip(), flags=re.S)
    _write(HEADER + body.strip() + "\n")
    return f"saved ({len(HEADER + body)} chars)"


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
