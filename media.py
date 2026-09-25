"""Shared media helpers: game-window screenshots and neural narration (edge-tts)."""
import asyncio
import hashlib
import importlib.util
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
FRAMES = os.path.join(STATE, "frames")
TTS_DIR = os.path.join(STATE, "tts")
VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural",
          "en-US-GuyNeural", "en-US-DavisNeural", "en-GB-SoniaNeural", "en-US-AriaNeural"]

# optional packages/tools: import name -> (pip package, what breaks without it); None = a program on PATH
OPTIONAL = {
    "mcp": ("mcp", "the autopilot itself (the game tools the model plays with)"),
    "edge_tts": ("edge-tts", "narration voice (dashboard read-aloud, chronicle video)"),
    "PIL": ("pillow", "game screenshots and chronicle video"),
    "win32gui": ("pywin32", "game screenshots"),
    "ffmpeg": (None, "chronicle video"),
}


def dependencies():
    """Which optional packages are installed: [{name, install, feature, ok}]."""
    out = []
    for name, (pip, feature) in OPTIONAL.items():
        ok = bool(shutil.which(name)) if pip is None else importlib.util.find_spec(name) is not None
        install = f"pip install {pip}" if pip else f"install {name} and put it on PATH (e.g. winget install Gyan.FFmpeg)"
        out.append({"name": name, "install": install, "feature": feature, "ok": ok})
    return out


def install(name):
    """Install one entry of OPTIONAL into the Python running this code (ffmpeg via winget).
    Only names from OPTIONAL are accepted. Returns {"ok", "log"}."""
    import subprocess
    import sys
    if name not in OPTIONAL:
        return {"ok": False, "log": f"unknown package {name!r}"}
    pip = OPTIONAL[name][0]

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr)[-3000:]

    if pip is None:  # a program, not a Python package
        if not shutil.which("winget"):
            return {"ok": False, "log": "winget is not available; install ffmpeg manually and put it on PATH"}
        rc, log = run(["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
                       "--accept-package-agreements", "--accept-source-agreements"])
        return {"ok": rc == 0, "log": log + ("\nRestart the dashboard and autopilot so they see the new PATH." if rc == 0 else "")}
    if importlib.util.find_spec("pip") is None:
        run([sys.executable, "-m", "ensurepip", "--upgrade"])
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pip]
    rc, log = run(cmd)
    if rc and "externally-managed-environment" in log:  # PEP 668 Pythons (uv, distro): install anyway
        rc, log = run(cmd + ["--break-system-packages"])
    importlib.invalidate_caches()
    return {"ok": rc == 0 and importlib.util.find_spec(name) is not None, "log": log}


def find_game_window():
    import win32gui
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and "Civilization VII" in win32gui.GetWindowText(h):
            found.append(h)
    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def capture_game(path):
    """Screenshot the Civ VII window (works even when it's behind other windows). Returns path or None."""
    try:
        import ctypes
        import win32gui
        import win32ui
        from PIL import Image
        h = find_game_window()
        if not h or win32gui.IsIconic(h):
            return None
        _, _, w, hgt = win32gui.GetClientRect(h)
        if w < 100 or hgt < 100:
            return None
        hdc = win32gui.GetWindowDC(h)
        mdc = win32ui.CreateDCFromHandle(hdc)
        sdc = mdc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mdc, w, hgt)
        sdc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(h, sdc.GetSafeHdc(), 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        info = bmp.GetInfo()
        im = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle()); sdc.DeleteDC(); mdc.DeleteDC(); win32gui.ReleaseDC(h, hdc)
        if sum(im.convert("L").resize((16, 9)).getdata()) < 16 * 9 * 3:  # all black: capture failed
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im.save(path, quality=90)
        return path
    except Exception:
        return None


def tts_mp3(text, voice="en-GB-RyanNeural", rate="-6%", pitch="-3Hz"):
    """Neural narration via Microsoft Edge read-aloud voices. Cached per text+voice. Returns the mp3 path."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts is not installed: pip install edge-tts") from None
    voice = voice if voice in VOICES else VOICES[0]
    os.makedirs(TTS_DIR, exist_ok=True)
    path = os.path.join(TTS_DIR, hashlib.sha1((voice + "|" + text).encode("utf-8")).hexdigest()[:16] + ".mp3")
    if not os.path.exists(path):
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(path + ".tmp"))
        os.replace(path + ".tmp", path)
    return path
