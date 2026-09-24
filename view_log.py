"""Pretty-print an agent session transcript (logs/*.jsonl): tool calls, results, text."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for path in sys.argv[1:]:
    print("==", path)
    for line in open(path, encoding="utf-8"):
        try:
            ev = json.loads(line)
        except ValueError:
            print("RAW", line[:200].rstrip()); continue
        t = ev.get("type")
        if t == "assistant":
            for c in ev["message"].get("content", []):
                if c.get("type") == "tool_use":
                    print("TOOL", c["name"].replace("mcp__civ7__", ""), json.dumps(c["input"])[:160])
                elif c.get("type") == "text":
                    print("TEXT", c["text"][:300].replace("\n", " "))
        elif t == "user":
            for c in ev["message"].get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    x = c.get("content"); x = x if isinstance(x, str) else json.dumps(x)
                    print("   ->", x[:180])
        elif t == "result":
            print("RESULT", ev.get("subtype"), ev.get("total_cost_usd"), str(ev.get("result", ""))[:300])
