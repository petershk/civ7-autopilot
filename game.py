"""High-level Civ VII game bridge: injects bot.js into the App UI context and calls CB.* functions."""
import json, os, threading
from tuner import Tuner

HERE = os.path.dirname(os.path.abspath(__file__))
BOT_FILES = [os.path.join(HERE, f) for f in ("bot.js", "bot2.js", "bot3.js", "bot4.js")]
BOT_VERSION = "4"

class Game:
    def __init__(self):
        self.t = Tuner()
        self._js_mtime = None
        # One game connection is shared by concurrent tool calls (the MCP server runs tools in threads):
        # serialise everything so injection and calls never interleave.
        self._lock = threading.RLock()

    def raw(self, js, timeout=30):
        with self._lock:
            return self.t.eval(js, timeout=timeout)

    def ensure(self):
        with self._lock:
            self._ensure()

    def _ensure(self):
        mtime = max(os.path.getmtime(f) for f in BOT_FILES)
        stamp = f"{BOT_VERSION}:{int(mtime)}"
        if self._js_mtime == stamp:
            return
        have = self.raw("typeof CB === 'undefined' || typeof CB.autoResolve !== 'function' ? '' : String(CB.stamp)")
        if have != stamp:
            for path in BOT_FILES:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
                r = self.raw(src, timeout=60)
                if not str(r).startswith("CB installed"):
                    raise RuntimeError(f"{os.path.basename(path)} injection failed: {r[:500]}")
            self.raw(f"CB.stamp = '{stamp}'")
        self._js_mtime = stamp

    def call(self, fn, *args, timeout=30):
        """Call CB.<fn>(*args) and return decoded JSON."""
        with self._lock:
            return self._call(fn, *args, timeout=timeout)

    def _call(self, fn, *args, timeout=30):
        self._js_mtime = None if getattr(self, "_force", False) else self._js_mtime
        try:
            self.ensure()
        except (OSError, ConnectionError):
            self.t.sock = None
            self._js_mtime = None
            self.ensure()
        a = ",".join(json.dumps(x) for x in args)
        js = f"(()=>{{try{{const r=CB.{fn}({a});return JSON.stringify(r===undefined?null:r)}}catch(e){{return JSON.stringify({{error:String(e),stack:String(e.stack||'').slice(0,400)}})}}}})()"
        r = self.raw(js, timeout=timeout)
        if "CB is not defined" in r or "is not a function" in r:
            self._js_mtime = None  # UI reloaded (load/age transition): reinject once and retry
            self.ensure()
            r = self.raw(js, timeout=timeout)
        try:
            return json.loads(r)
        except ValueError:
            return {"error": "non-json reply", "raw": r[:1000]}

if __name__ == "__main__":
    import sys
    g = Game()
    fn = sys.argv[1]
    def parse(a):
        try:
            return json.loads(a)
        except ValueError:
            return a
    args = [parse(a) for a in sys.argv[2:]]
    print(json.dumps(g.call(fn, *args), indent=1))
