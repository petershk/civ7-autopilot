"""Minimal Civ VII FireTuner (Tuner4) client: evaluate JavaScript in the game's UI contexts."""
import os, socket, struct, json, sys, threading, time, urllib.parse

HOST, PORT = "127.0.0.1", 4318

class Tuner:
    def __init__(self, host=HOST, port=PORT):
        self.host, self.port = host, port
        self.sock = None
        self.lock = threading.Lock()
        self.states = {}

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self._send(4, "LSQ:")
        tag, payload = self._recv(10)
        parts = [p for p in payload.split("\x00") if p]
        self.states = {parts[i + 1]: int(parts[i]) for i in range(0, len(parts) - 1, 2)}
        return self.states

    def _send(self, tag, text):
        d = text.encode("utf-8") + b"\x00"
        self.sock.sendall(struct.pack("<Ii", len(d), tag) + d)

    def _recvn(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("tuner socket closed")
            buf += c
        return buf

    def _recv(self, timeout):
        self.sock.settimeout(timeout)
        n, tag = struct.unpack("<Ii", self._recvn(8))
        return tag, self._recvn(n).rstrip(b"\x00").decode("utf-8", "replace")

    def eval(self, js, state="App UI", timeout=30):
        """Evaluate a JS expression; returns its string result (objects come back as JSON)."""
        with self.lock:
            if self.sock is None:
                self.connect()
            # Percent-encode so no quotes/backslashes cross the wire (the tuner mangles escapes).
            # Every reply is prefixed with a unique tag so stale/unsolicited messages can never be
            # mistaken for this call's answer (otherwise replies can shift by one and stay desynced).
            self.seq = getattr(self, "seq", 0) + 1
            tag = f"#{os.getpid()}.{self.seq}#"
            enc = urllib.parse.quote(js, safe="")
            esc = (f"(()=>{{let r;try{{r=(0,eval)(decodeURIComponent('{enc}'))}}catch(e){{r=String(e)}}"
                   f"return '{tag}'+(typeof r=='object'&&r!==null?JSON.stringify(r):String(r))}})()")
            idx = self.states.get(state, state if isinstance(state, int) else 65535)
            for attempt in range(3):
                try:
                    if self.sock is None:
                        self.connect()
                    self._send(3, f"CMD:{idx}:{esc}")
                    deadline = time.time() + timeout
                    while True:
                        payload = self._recv(max(0.5, deadline - time.time()))[1]
                        if payload.startswith(tag):
                            return payload[len(tag):]
                        # stale reply from an earlier call or unsolicited output: skip it
                except socket.timeout:
                    self.sock = None
                    raise
                except (OSError, ConnectionError):
                    # transient reset (game busy / reloading UI): reconnect and retry
                    self.sock = None
                    if attempt == 2:
                        raise
                    time.sleep(1.5 * (attempt + 1))

    def evalj(self, js, **kw):
        r = self.eval(js, **kw)
        try:
            return json.loads(r)
        except ValueError:
            return r

if __name__ == "__main__":
    t = Tuner(); print(t.connect())
    for a in sys.argv[1:]:
        print(t.eval(a))
