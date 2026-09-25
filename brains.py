"""Pluggable "brains" for the Civ VII autopilot.

A brain spec is "provider:model", e.g.
    claude:sonnet   claude:opus          (Claude Code CLI - default)
    codex:gpt-5.5   codex:               (OpenAI Codex CLI; empty model = its default)
    gemini:gemini-2.5-pro                (Gemini CLI)
    ollama:qwen3.8                       (local model via Ollama's OpenAI-compatible API, built-in loop)
    openai:gpt-5.1                       (OpenAI API, needs OPENAI_API_KEY, built-in loop)
    openrouter:anthropic/claude-sonnet-5 (OpenRouter, needs OPENROUTER_API_KEY, built-in loop)

Every brain gets the same civ7 MCP tools and nothing else (no shell / file access), and writes a
transcript in Claude's stream-json shape to logs/<tag>.jsonl so the dashboard and view_log.py work
for all of them. run_brain() returns (result_text, tool_calls, cost_usd_or_None).
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_SCRIPT = os.path.join(HERE, "civ_mcp.py").replace("\\", "/")
PY = sys.executable.replace("\\", "/")

OPENAI_COMPAT = {
    "ollama": ("http://localhost:11434/v1", None),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}


def parse_spec(spec):
    prov, _, model = (spec or "claude:sonnet").partition(":")
    if not model and prov not in ("claude", "codex", "gemini") and prov not in OPENAI_COMPAT:
        prov, model = "claude", prov  # bare "sonnet"/"opus" = claude
    return prov.lower(), model


def ensure_mcp_config():
    """Write mcp.json (claude) and .gemini/settings.json for this machine's paths."""
    cfg = {"mcpServers": {"civ7": {"command": PY, "args": [MCP_SCRIPT]}}}
    with open(os.path.join(HERE, "mcp.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    gdir = os.path.join(HERE, ".gemini")
    os.makedirs(gdir, exist_ok=True)
    with open(os.path.join(gdir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"civ7": {"command": PY, "args": [MCP_SCRIPT], "trust": True, "timeout": 120000}}}, f, indent=1)
    with open(os.path.join(gdir, "civ7-policy.toml"), "w", encoding="utf-8") as f:
        f.write('# Only the civ7 game tools may run; every built-in tool (shell, files, web) is denied.\n'
                '[[rule]]\nmcpName = "civ7"\ndecision = "allow"\npriority = 900\n\n'
                '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 800\n'
                'denyMessage = "Only the civ7 game tools are available in this session."\n')


# ------------------------------------------------------------------ transcript helpers
class Transcript:
    """Writes events in Claude stream-json shape."""

    def __init__(self, path, brain):
        self.f = open(path, "w", encoding="utf-8")
        self.tools = 0
        self.emit({"type": "system", "subtype": "init", "brain": brain})

    def emit(self, ev):
        self.f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.f.flush()

    def text(self, t):
        if t and t.strip():
            self.emit({"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}})

    def tool_use(self, tid, name, args):
        self.tools += 1
        self.emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "mcp__civ7__" + name, "input": args}]}})

    def tool_result(self, tid, content):
        self.emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}})

    def result(self, text, cost=None, **extra):
        self.emit({"type": "result", "subtype": "success", "result": text, "total_cost_usd": cost, **extra})
        self.f.close()


def _exe(name):
    """Resolve CLI shims on Windows (npm installs gemini as gemini.cmd)."""
    return shutil.which(name) or shutil.which(name + ".cmd") or name


def _run_stream(cmd, on_line, timeout, env=None, stdin_text=None, errlog=None):
    """Run a CLI, feeding each stdout line to on_line; returns returncode or 'timeout'.
    Long prompts go through stdin (Windows caps command lines at ~32K chars)."""
    cmd = [_exe(cmd[0])] + cmd[1:]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog or subprocess.DEVNULL,
                         stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                         cwd=HERE, env=env, text=True, encoding="utf-8", errors="replace")
    if stdin_text is not None:
        p.stdin.write(stdin_text)
        p.stdin.close()
    t0 = time.time()
    try:
        for line in p.stdout:
            on_line(line)
            if time.time() - t0 > timeout:
                p.kill()
                return "timeout"
        return p.wait(timeout=max(1, timeout - (time.time() - t0)))
    except subprocess.TimeoutExpired:
        p.kill()
        return "timeout"


# ------------------------------------------------------------------ claude
CAPPED = "[hit round cap] "


def run_claude(prompt, system, model, logfile, timeout, max_rounds=None):
    cmd = ["claude", "-p", prompt, "--model", model or "sonnet",
           "--mcp-config", os.path.join(HERE, "mcp.json"), "--strict-mcp-config",
           "--allowedTools", "mcp__civ7", "--tools", "", "--setting-sources", "project,local",
           "--append-system-prompt", system, "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if max_rounds:
        cmd += ["--max-turns", str(max_rounds)]
    out = {"result": "", "tools": 0, "cost": None}
    with open(logfile, "w", encoding="utf-8") as lf:
        def on(line):
            lf.write(line); lf.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                return
            if ev.get("type") == "assistant":
                out["tools"] += sum(1 for c in ev.get("message", {}).get("content", []) if c.get("type") == "tool_use")
            elif ev.get("type") == "result":
                out["result"], out["cost"] = ev.get("result", "") or "", ev.get("total_cost_usd")
                if ev.get("subtype") == "error_max_turns":
                    out["result"] = CAPPED + out["result"]
        rc = _run_stream(cmd, on, timeout)
    return out["result"], out["tools"], out["cost"], rc


# ------------------------------------------------------------------ codex
def run_codex(prompt, system, model, logfile, timeout):
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--json", "-s", "read-only",
           "-c", 'approval_policy="never"',
           "-c", f'mcp_servers.civ7.command="{PY}"', "-c", f'mcp_servers.civ7.args=["{MCP_SCRIPT}"]',
           "-c", 'mcp_servers.civ7.default_tools_approval_mode="approve"',
           "-c", "mcp_servers.civ7.startup_timeout_sec=60", "-c", "mcp_servers.civ7.tool_timeout_sec=180"]
    if model:
        cmd += ["-m", model]
    cmd.append("-")  # prompt comes from stdin
    tr = Transcript(logfile, f"codex:{model}")
    last = {"text": "", "usage": None}

    def on(line):
        try:
            ev = json.loads(line)
        except ValueError:
            return
        it = ev.get("item") or {}
        if ev.get("type") == "item.started" and it.get("type") == "mcp_tool_call":
            tr.tool_use(it.get("id"), it.get("tool", "?"), it.get("arguments") or {})
        elif ev.get("type") == "item.completed":
            if it.get("type") == "mcp_tool_call":
                res = it.get("result") or {}
                txt = " ".join(c.get("text", "") for c in (res.get("content") or []) if isinstance(c, dict)) or json.dumps(it.get("error"))
                tr.tool_result(it.get("id"), txt[:20000])
            elif it.get("type") in ("agent_message", "reasoning"):
                if it.get("type") == "agent_message":
                    last["text"] = it.get("text", "")
                tr.text(it.get("text", ""))
            elif it.get("type") == "command_execution":
                tr.text(f"[codex ran a local command (read-only sandbox): {str(it.get('command'))[:120]}]")
        elif ev.get("type") == "turn.completed":
            last["usage"] = ev.get("usage")
    with open(logfile + ".stderr", "w", encoding="utf-8") as el:
        rc = _run_stream(cmd, on, timeout, stdin_text=f"{system}\n\n---\n\n{prompt}", errlog=el)
    tr.result(last["text"], None, usage=last["usage"])
    return last["text"], tr.tools, None, rc


# ------------------------------------------------------------------ gemini
def run_gemini(prompt, system, model, logfile, timeout):
    cmd = ["gemini", "-p", "Follow the instructions above and use the civ7 tools.", "--approval-mode", "default",
           "--policy", os.path.join(HERE, ".gemini", "civ7-policy.toml"), "--allowed-mcp-server-names", "civ7",
           "-o", "stream-json"]
    if model:
        cmd += ["-m", model]
    env = dict(os.environ, GEMINI_CLI_TRUST_WORKSPACE="true")
    tr = Transcript(logfile, f"gemini:{model}")
    buf = {"text": "", "final": "", "stats": None}

    def flush():
        if buf["text"]:
            tr.text(buf["text"]); buf["final"] = buf["text"]; buf["text"] = ""

    def on(line):
        try:
            ev = json.loads(line)
        except ValueError:
            return
        t = ev.get("type")
        if t == "message" and ev.get("role") == "assistant":
            buf["text"] += ev.get("content", "")
        elif t == "tool_use":
            flush()
            name = ev.get("tool_name", "?")
            tr.tool_use(ev.get("tool_id"), name.replace("mcp_civ7_", ""), ev.get("parameters") or {})
        elif t == "tool_result":
            tr.tool_result(ev.get("tool_id"), str(ev.get("output") or ev.get("status"))[:20000])
        elif t == "result":
            flush(); buf["stats"] = ev.get("stats")
    with open(logfile + ".stderr", "w", encoding="utf-8") as el:
        rc = _run_stream(cmd, on, timeout, env=env, stdin_text=f"{system}\n\n---\n\n{prompt}", errlog=el)
    flush()
    tr.result(buf["final"], None, stats=buf["stats"])
    return buf["final"], tr.tools, None, rc


# ------------------------------------------------------------------ built-in loop (OpenAI-compatible)
def run_openai_compat(prompt, system, provider, model, logfile, timeout, max_steps=80):
    """Minimal agent loop: the civ7 tools are loaded in-process from civ_mcp and offered as functions."""
    import asyncio
    from openai import OpenAI
    import civ_mcp

    base, key_env = OPENAI_COMPAT[provider]
    base = os.environ.get(f"{provider.upper()}_BASE_URL", base)
    key = os.environ.get(key_env) if key_env else "ollama"
    if key_env and not key:
        raise RuntimeError(f"{key_env} is not set")
    client = OpenAI(base_url=base, api_key=key, timeout=600)

    tools_meta = asyncio.run(civ_mcp.app.list_tools())
    tools = [{"type": "function", "function": {"name": t.name, "description": (t.description or "")[:1000],
                                               "parameters": t.input_schema or {"type": "object", "properties": {}}}}
             for t in tools_meta]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    tr = Transcript(logfile, f"{provider}:{model}")
    t0, final = time.time(), ""
    for _ in range(max_steps):
        if time.time() - t0 > timeout:
            break
        r = client.chat.completions.create(model=model, messages=msgs, tools=tools)
        m = r.choices[0].message
        msgs.append({"role": "assistant", "content": m.content or "", **({"tool_calls": [tc.model_dump() for tc in m.tool_calls]} if m.tool_calls else {})})
        if m.content:
            tr.text(m.content); final = m.content
        if not m.tool_calls:
            break
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            tr.tool_use(tc.id, tc.function.name, args)
            try:
                res = asyncio.run(civ_mcp.app.call_tool(tc.function.name, args))
                out = " ".join(getattr(c, "text", "") for c in (getattr(res, "content", None) or [])) or str(res)
            except Exception as e:
                out = json.dumps({"error": str(e)})
            tr.tool_result(tc.id, out[:20000])
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out[:12000]})
    tr.result(final, None)
    return final, tr.tools, None, 0


# ------------------------------------------------------------------ entry point
def run_brain(spec, prompt, system, logfile, timeout=1500, max_rounds=None):
    """max_rounds caps the model's tool-use rounds (Claude only); a capped session's result starts with CAPPED."""
    prov, model = parse_spec(spec)
    if prov == "claude":
        return run_claude(prompt, system, model, logfile, timeout, max_rounds)
    if prov == "codex":
        return run_codex(prompt, system, model, logfile, timeout)
    if prov == "gemini":
        return run_gemini(prompt, system, model, logfile, timeout)
    if prov in OPENAI_COMPAT:
        return run_openai_compat(prompt, system, prov, model, logfile, timeout)
    raise ValueError(f"unknown brain provider '{prov}' in spec '{spec}'")


def ask(spec, prompt, timeout=300):
    """One-shot question without game tools (leader pick, age-transition civ pick)."""
    prov, model = parse_spec(spec)
    try:
        if prov == "claude":
            out = subprocess.run(["claude", "-p", prompt, "--model", model or "sonnet", "--tools", "",
                                  "--setting-sources", "project,local", "--no-session-persistence"],
                                 capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, encoding="utf-8")
            return out.stdout
        if prov in OPENAI_COMPAT:
            from openai import OpenAI
            base, key_env = OPENAI_COMPAT[prov]
            c = OpenAI(base_url=base, api_key=os.environ.get(key_env) if key_env else "ollama")
            return c.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}]).choices[0].message.content
        tmp = os.path.join(HERE, "logs", f"ask_{int(time.time())}.jsonl")
        return run_brain(spec, prompt, "Answer the question directly.", tmp, timeout)[0]
    except Exception as e:
        return f"(ask failed: {e})"


# ------------------------------------------------------------------ discovery (for the dashboard pickers)
KNOWN = {
    "claude": [("claude:sonnet", "Claude Sonnet"), ("claude:opus", "Claude Opus"), ("claude:haiku", "Claude Haiku")],
    "codex": [("codex:", "Codex (its default model)")],
    "gemini": [("gemini:gemini-2.5-pro", "Gemini 2.5 Pro"), ("gemini:gemini-2.5-flash", "Gemini 2.5 Flash")],
    "openai": [("openai:gpt-5.1", "OpenAI GPT-5.1 (API)")],
    "openrouter": [("openrouter:anthropic/claude-sonnet-5", "OpenRouter: Claude Sonnet 5"), ("openrouter:google/gemini-2.5-pro", "OpenRouter: Gemini 2.5 Pro")],
}


_checked = {}


def _get_json(url, key=None, timeout=5):
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check(spec):
    """Why this brain can't run on THIS machine, or "" if it can. Cached for a minute (it may hit the network).
    Verifies the CLI is installed / the API key is set, and for API brains that the model exists."""
    hit = _checked.get(spec)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    prov, model = ("jev", "") if (spec or "").split(":")[0] == "jev" else parse_spec(spec)
    problem = ""
    if prov == "jev":
        import jev
        problem = "" if jev.available() else "Jev needs DEFAPI_KEY (defapi.org); routine turns fall back to the turn brain"
    elif prov in ("claude", "codex", "gemini"):
        if not (shutil.which(prov) or shutil.which(prov + ".cmd")):
            problem = f"the {prov} CLI is not installed (not on PATH)"
    elif prov == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rsplit("/v1", 1)[0]
        try:
            names = {m.get("name", "") for m in _get_json(base + "/api/tags", timeout=3).get("models", [])}
            if model not in names and f"{model}:latest" not in names:
                problem = f"Ollama model {model!r} is not pulled (ollama pull {model})"
        except Exception as e:
            problem = f"Ollama is not reachable at {base} ({e})"
    elif prov in OPENAI_COMPAT:
        base, key_env = OPENAI_COMPAT[prov]
        base = os.environ.get(f"{prov.upper()}_BASE_URL", base)
        key = os.environ.get(key_env)
        if not key:
            problem = f"{key_env} is not set"
        else:
            try:
                ids = {m.get("id") for m in _get_json(base + "/models", key).get("data", [])}
                if ids and model not in ids:
                    problem = f"{prov} has no model {model!r} for this key"
            except Exception as e:  # can't verify (offline, rate limit): don't block on it
                if getattr(e, "code", None) == 401:
                    problem = f"{key_env} was rejected (401)"
    else:
        problem = f"unknown brain provider {prov!r}"
    _checked[spec] = (time.time(), problem)
    return problem


def available():
    """Brains usable on THIS machine: installed CLIs, API keys present, Ollama models pulled, plus brains.json extras."""
    out = []
    for cli in ("claude", "codex", "gemini"):
        if shutil.which(cli) or shutil.which(cli + ".cmd"):
            out += [{"spec": s, "label": l, "provider": cli} for s, l in KNOWN[cli]]
    for prov in ("openai", "openrouter"):
        if os.environ.get(OPENAI_COMPAT[prov][1]):
            out += [{"spec": s, "label": l, "provider": prov} for s, l in KNOWN[prov]]
    try:  # Ollama: list whatever models this user has pulled
        import urllib.request
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rsplit("/v1", 1)[0]
        with urllib.request.urlopen(base + "/api/tags", timeout=2) as r:
            for m in json.load(r).get("models", []):
                name = m.get("name", "")
                cloud = name.endswith(":cloud") or m.get("size", 1) == 0
                gb = m.get("size", 0) / 1e9
                out.append({"spec": f"ollama:{name}", "provider": "ollama",
                            "label": f"Ollama {name}" + (" (cloud)" if cloud else f" (local, {gb:.0f} GB)")})
    except Exception:
        pass
    try:  # user-defined extras: [{"spec": "openai:gpt-4.1", "label": "My GPT"}]
        with open(os.path.join(HERE, "brains.json"), encoding="utf-8") as f:
            out += [dict(x, provider=x.get("provider", parse_spec(x["spec"])[0])) for x in json.load(f)]
    except (OSError, ValueError, KeyError):
        pass
    return out
