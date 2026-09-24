"""Premium media for the chronicle video via the Gemini API (needs GEMINI_API_KEY):
  - narration: Gemini TTS, directed like an actor ("Augustus by candlelight...")
  - illustrations: Gemini image generation, an epic painting for big moments
  - music: Lyria, an original score matched to the civilization / age / mood
Everything is cached by content hash under state/premium/, so re-rendering a video costs nothing extra.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "state", "premium")
API = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

TTS_MODEL = os.environ.get("CIV_TTS_MODEL", "gemini-3.1-flash-tts-preview")
IMAGE_MODEL = os.environ.get("CIV_IMAGE_MODEL", "gemini-3.1-flash-image")
MUSIC_MODEL = os.environ.get("CIV_MUSIC_MODEL", "lyria-3.5")
GEMINI_VOICES = ["Charon", "Algenib", "Gacrux", "Orus", "Fenrir", "Iapetus", "Kore", "Sulafat"]

# rough list prices (USD) for the cost estimate shown in the dashboard
PRICE = {"tts_per_entry": 0.006, "image": 0.04, "music_track": 0.06}


def available():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _gen(model, body, timeout=300):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    req = urllib.request.Request(API.format(model), data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{model}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")


def _inline(resp, prefix):
    for p in (resp.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
        d = p.get("inlineData")
        if d and d.get("mimeType", "").startswith(prefix):
            return d["mimeType"], base64.b64decode(d["data"])
    raise RuntimeError(f"no {prefix} in response: {json.dumps(resp)[:300]}")


def _path(kind, key, ext):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"{kind}_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}.{ext}")


def narrate(text, voice="Charon", leader="Augustus", civ="Rome"):
    """Directed Gemini TTS -> mp3 path."""
    voice = voice if voice in GEMINI_VOICES else "Charon"
    direction = (f"Narrate as {leader}, ruler of {civ}, recording the royal chronicle by candlelight: grave, measured, "
                 f"quietly proud, with natural dramatic pauses; let triumphs swell and losses weigh. Text: ")
    out = _path("tts", voice + "|" + direction + text, "mp3")
    if os.path.exists(out):
        return out
    r = _gen(TTS_MODEL, {"contents": [{"parts": [{"text": direction + text}]}],
                         "generationConfig": {"responseModalities": ["AUDIO"],
                                              "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}})
    mime, pcm = _inline(r, "audio")
    rate = 24000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass
    wav = out[:-4] + ".wav"
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", wav, "-b:a", "160k", out], check=True)
    os.remove(wav)
    return out


def illustrate(entry_text, headline="", civ="Rome", age="Antiquity"):
    """Epic painting of the moment -> jpg path (16:9)."""
    prompt = (f"Epic historical oil painting, cinematic wide 16:9 composition, dramatic lighting, rich detail, no text, "
              f"no captions, no modern elements. Setting: the {civ} civilization in the {age} age. "
              f"Depict this moment from the ruler's chronicle{': ' + headline if headline else ''}. {entry_text}")
    out = _path("img", prompt, "jpg")
    if os.path.exists(out):
        return out
    r = _gen(IMAGE_MODEL, {"contents": [{"parts": [{"text": prompt}]}],
                           "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}})
    _, data = _inline(r, "image")
    with open(out, "wb") as f:
        f.write(data)
    return out


def score(civ="Rome", age="Antiquity", mood="stately"):
    """Original instrumental score (~1 min, looped under the video) -> mp3 path."""
    prompt = (f"Instrumental cinematic film score for a historical strategy chronicle of {civ} in the {age} age. "
              f"Mood: {mood}. Orchestral with period-evocative instruments, slow build, loopable, no vocals, no lyrics.")
    out = _path("music", prompt, "mp3")
    if os.path.exists(out):
        return out
    r = _gen(MUSIC_MODEL, {"contents": [{"parts": [{"text": prompt}]}]}, timeout=600)
    mime, data = _inline(r, "audio")
    raw = out if "mpeg" in mime or "mp3" in mime else out[:-4] + "." + mime.split("/")[-1].split(";")[0]
    with open(raw, "wb") as f:
        f.write(data)
    if raw != out:
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", raw, "-b:a", "192k", out], check=True)
        os.remove(raw)
    return out


def estimate(n_entries, n_images, n_tracks=1):
    return round(n_entries * PRICE["tts_per_entry"] + n_images * PRICE["image"] + n_tracks * PRICE["music_track"], 2)


# ------------------------------------------------------------------ cinematic scenes (Veo)
VIDEO_MODEL = os.environ.get("CIV_VIDEO_MODEL", "veo-3.1-lite-generate-preview")
TEXT_MODEL = os.environ.get("CIV_TEXT_MODEL", "gemini-3.6-flash")
PRICE.update({"veo_clip": 0.50, "shot_prompt": 0.001})   # rough estimate per 8s lite clip
STYLES = {
    "painted": "Epic historical oil painting come to life, painterly textures, rich colour, dramatic lighting",
    "cinematic": "Photorealistic live-action historical epic film, 35mm cinematography, natural skin and fabric detail, "
                 "volumetric light, shallow depth of field, period-accurate costumes and armour",
}


def shot_prompt(entry_text, headline, civ, age, style="cinematic", gentle=False):
    """Turn a first-person chronicle entry into a concrete visual shot description (characters, action, camera)."""
    key = f"{style}|{civ}|{age}|{headline}|{entry_text}" + ("|gentle" if gentle else "")
    out = _path("shot", key, "txt")
    if os.path.exists(out):
        return open(out, encoding="utf-8").read()
    ask = (f"You are a film director. Turn this chronicle entry (written by the ruler of {civ}, {age} age) into ONE vivid "
           f"8-second shot for a video model. Describe concretely: the main characters (appearance, period costume), what they "
           f"are physically doing, the setting, weather/light, and one camera movement. Show the single most dramatic moment "
           f"(e.g. the battle, the founding, the council, the ceremony). Max 90 words, present tense, no dialogue, no on-screen "
           f"text, no names of game mechanics. Keep it PG-13 like a family historical epic: battles show clashing shields, "
           f"charges, arrows in flight, banners and smoke, but NO blood, gore, wounds, corpses or killing blows."
           + (" Be especially restrained: show the tense moment just before or after the clash, or the commanders "
              "watching from a ridge, rather than the fighting itself." if gentle else "")
           + f"\n\nHeadline: {headline or '(none)'}\nEntry: {entry_text}")
    r = _gen(TEXT_MODEL, {"contents": [{"parts": [{"text": ask}]}]}, timeout=120)
    text = "".join(p.get("text", "") for p in (r.get("candidates") or [{}])[0].get("content", {}).get("parts", [])).strip()
    text = f"{STYLES.get(style, STYLES['cinematic'])}. {text} No text, no captions, no watermarks."
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def still(shot, style="cinematic"):
    """First frame for the scene (keeps look/characters consistent with the animation)."""
    out = _path("still", style + "|" + shot, "jpg")
    if os.path.exists(out):
        return out
    r = _gen(IMAGE_MODEL, {"contents": [{"parts": [{"text": ("Single film still, 16:9. " if style == "cinematic" else "") + shot}]}],
                           "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}})
    _, data = _inline(r, "image")
    with open(out, "wb") as f:
        f.write(data)
    return out


def animate(image_path, shot, seconds=8):
    """Veo image-to-video: the still comes alive (motion, weather, camera move, ambient sound) -> mp4 path."""
    import time as _t
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    out = _path("veo", VIDEO_MODEL + "|" + shot + "|" + hashlib.sha1(img_b64.encode()).hexdigest(), "mp4")
    if os.path.exists(out):
        return out
    key = os.environ.get("GEMINI_API_KEY")
    hdr = {"Content-Type": "application/json", "x-goog-api-key": key}
    body = {"instances": [{"prompt": shot, "image": {"bytesBase64Encoded": img_b64, "mimeType": "image/jpeg"}}],
            "parameters": {"aspectRatio": "16:9", "durationSeconds": seconds, "resolution": "720p"}}
    base = "https://generativelanguage.googleapis.com/v1beta/"
    try:
        op = json.load(urllib.request.urlopen(urllib.request.Request(base + f"models/{VIDEO_MODEL}:predictLongRunning",
                                                                     data=json.dumps(body).encode(), headers=hdr), timeout=120))["name"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"veo: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    t0 = _t.time()
    while True:
        _t.sleep(8)
        st = json.load(urllib.request.urlopen(urllib.request.Request(base + op, headers=hdr), timeout=60))
        if st.get("done"):
            break
        if _t.time() - t0 > 900:
            raise RuntimeError("veo: timed out")
    if st.get("error"):
        raise RuntimeError(f"veo: {st['error']}")
    samples = st.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
    if not samples:
        raise RuntimeError(f"veo: no video returned (possibly filtered): {json.dumps(st)[:300]}")
    data = urllib.request.urlopen(urllib.request.Request(samples[0]["video"]["uri"], headers={"x-goog-api-key": key}), timeout=300).read()
    with open(out, "wb") as f:
        f.write(data)
    return out


def scene(entry_text, headline, civ, age, style="cinematic", image=None):
    """Full pipeline for one big moment: directed shot -> first frame -> animated clip. Returns (mp4, still).
    If the video model's safety filter rejects the shot, it is re-directed more gently and retried once."""
    for gentle in (False, True):
        shot = shot_prompt(entry_text, headline, civ, age, style, gentle=gentle)
        first = image or still(shot, style)
        try:
            return animate(first, shot), first
        except RuntimeError as e:
            if gentle or "filter" not in str(e).lower():
                raise
