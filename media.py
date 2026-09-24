"""Shared media helpers: game-window screenshots and neural narration (edge-tts)."""
import asyncio
import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
FRAMES = os.path.join(STATE, "frames")
TTS_DIR = os.path.join(STATE, "tts")
VOICES = ["en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural",
          "en-US-GuyNeural", "en-US-DavisNeural", "en-GB-SoniaNeural", "en-US-AriaNeural"]


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
    import edge_tts
    voice = voice if voice in VOICES else VOICES[0]
    os.makedirs(TTS_DIR, exist_ok=True)
    path = os.path.join(TTS_DIR, hashlib.sha1((voice + "|" + text).encode("utf-8")).hexdigest()[:16] + ".mp3")
    if not os.path.exists(path):
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(path + ".tmp"))
        os.replace(path + ".tmp", path)
    return path
