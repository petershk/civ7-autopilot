"""Chronicle video: turns the agent's in-character chronicle into a narrated MP4.

Each chronicle entry becomes a clip: the game screenshot taken when it was written (slow Ken Burns zoom),
a caption layer (turn, headline, text) and neural narration. A title card opens, a standings card closes.
Optional background music: put any .mp3 files in state/music/ (mixed quietly under the narration).

    python video.py [--from 1] [--to 999] [--voice en-GB-RyanNeural]
"""
import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import textwrap
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import media
import premium

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
OUT_DIR = os.path.join(STATE, "videos")
W, H, FPS = 1280, 720, 30
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def font(names, size):
    for n in names:
        p = os.path.join(FONT_DIR, n)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


SERIF = lambda s: font(["georgia.ttf", "times.ttf"], s)          # noqa: E731
SERIF_B = lambda s: font(["georgiab.ttf", "timesbd.ttf"], s)     # noqa: E731
SANS = lambda s: font(["segoeui.ttf", "arial.ttf"], s)           # noqa: E731


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")
    return r


def duration(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path])
    return float(r.stdout.strip() or 0)


def read_chronicle():
    out = []
    try:
        for line in open(os.path.join(STATE, "chronicle.jsonl"), encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    by_turn = {}
    for e in out:  # keep the last entry per turn (retries may write twice)
        by_turn[e.get("turn")] = e
    return [by_turn[t] for t in sorted(k for k in by_turn if k is not None)]


def caption_layer(entry, path):
    """Transparent 1280x720 overlay: bottom gradient with turn / headline / wrapped text."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = Image.new("RGBA", (W, 330), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    for y in range(330):
        gd.line([(0, y), (W, y)], fill=(8, 8, 12, int(225 * min(1, y / 150))))
    im.alpha_composite(g, (0, H - 330))
    d = ImageDraw.Draw(im)
    y = H - 250
    d.text((56, y), f"TURN {entry.get('turn')}", font=SANS(20), fill=(227, 179, 65, 255))
    y += 30
    if entry.get("headline"):
        d.text((56, y), entry["headline"], font=SERIF_B(34), fill=(255, 217, 122, 255))
        y += 46
    # shrink the text until the whole entry fits above the bottom edge
    for size, width, step in ((25, 86, 33), (22, 98, 29), (20, 108, 26), (18, 120, 23)):
        lines = textwrap.wrap(entry.get("entry", ""), width=width)
        if y + len(lines) * step <= H - 16:
            break
    for ln in lines[:12]:
        d.text((56, y), ln, font=SERIF(size), fill=(240, 236, 225, 255))
        y += step
    im.save(path)


def text_card(entry, path, title=None, subtitle=None):
    """Full-frame card for entries without a screenshot (and for title/end cards)."""
    im = Image.new("RGB", (W, H), (22, 18, 12))
    d = ImageDraw.Draw(im)
    for r in range(0, 900, 6):  # vignette
        d.ellipse([W / 2 - r * 1.6, H / 2 - r, W / 2 + r * 1.6, H / 2 + r], outline=(max(10, 46 - r // 22), max(8, 38 - r // 24), max(5, 24 - r // 30)))
    im = im.filter(ImageFilter.GaussianBlur(3))
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, W - 40, H - 40], outline=(138, 109, 29), width=2)
    if title:
        tw = d.textlength(title, font=SERIF_B(64))
        d.text(((W - tw) / 2, H / 2 - 90), title, font=SERIF_B(64), fill=(255, 217, 122))
        if subtitle:
            for i, ln in enumerate(subtitle.split("\n")):
                sw = d.textlength(ln, font=SERIF(28))
                d.text(((W - sw) / 2, H / 2 + 10 + i * 40), ln, font=SERIF(28), fill=(220, 206, 170))
    else:
        d.text((90, 90), f"TURN {entry.get('turn')}", font=SANS(22), fill=(227, 179, 65))
        y = 150
        if entry.get("headline"):
            d.text((90, y), entry["headline"], font=SERIF_B(40), fill=(255, 217, 122)); y += 70
        for ln in textwrap.wrap(entry.get("entry", ""), width=62)[:9]:
            d.text((90, y), ln, font=SERIF(32), fill=(236, 228, 208)); y += 46
    im.save(path, quality=92)


def make_clip(image, overlay, audio, out, pad=1.0, zoom=True):
    dur = duration(audio) + pad
    frames = int(dur * FPS)
    if zoom:
        # gentle push-in toward a random point: Ken Burns
        zx, zy = random.choice([(0.5, 0.5), (0.45, 0.4), (0.55, 0.45), (0.5, 0.35)])
        vf = (f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='min(1+0.0009*on,1.12)':x='(iw-iw/zoom)*{zx}':y='(ih-ih/zoom)*{zy}':d={frames}:s={W}x{H}:fps={FPS}[bg]")
    else:
        vf = f"[0:v]scale={W}:{H},loop=loop={frames}:size=1:start=0,fps={FPS}[bg]"
    fc = vf + (";[bg][1:v]overlay=0:0,format=yuv420p[v]" if overlay else ";[bg]format=yuv420p[v]")
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", image]
    if overlay:
        cmd += ["-loop", "1", "-i", overlay]
    cmd += ["-i", audio, "-filter_complex", fc + f";[{2 if overlay else 1}:a]apad=pad_dur={pad},aresample=44100[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ac", "2", out]
    run(cmd)
    return dur


def make_anim_clip(scene_mp4, overlay, audio, out, pad=1.0, ambience=0.35):
    """Animated scene (Veo) + captions + narration; the scene's own ambient sound plays quietly underneath.
    If the narration outlasts the clip, the final frame holds."""
    dur = max(duration(audio) + pad, duration(scene_mp4))
    has_amb = "audio" in run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", scene_mp4]).stdout
    fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},tpad=stop_mode=clone:stop_duration={dur}[bg];"
          f"[bg][1:v]overlay=0:0,format=yuv420p[v];[2:a]apad=pad_dur={pad},aresample=44100[n]")
    if has_amb:
        fc += f";[0:a]volume={ambience},aresample=44100,apad[amb];[n][amb]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        fc += ";[n]anull[a]"
    run([FFMPEG, "-y", "-loglevel", "error", "-i", scene_mp4, "-loop", "1", "-i", overlay, "-i", audio,
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ac", "2", out])
    return dur


WAR_WORDS = ("war", "battle", "legion", "siege", "invader", "raid", "army", "armies", "blood", "sword", "attack")


def build(turn_from=1, turn_to=10 ** 6, voice="en-GB-RyanNeural", progress=lambda pct, msg: None,
          premium_mode=False, illustrate="headlines", gemini_voice="Charon", animate="none", style="painted"):
    """premium_mode: Gemini TTS narration + Lyria score + illustrations (illustrate: headlines|all|none).
    animate: none|headlines|all - Veo animated scenes for those entries. style: painted|cinematic (photoreal)."""
    entries = [e for e in read_chronicle() if turn_from <= (e.get("turn") or 0) <= turn_to]
    if not entries:
        raise RuntimeError("no chronicle entries in that range yet")
    os.makedirs(OUT_DIR, exist_ok=True)
    work = os.path.join(OUT_DIR, f"_work_{int(time.time())}")
    os.makedirs(work)
    game = {}
    try:
        game = json.load(open(os.path.join(STATE, "status.json"), encoding="utf-8")).get("overview", {})
    except (OSError, ValueError):
        pass
    leader = (game.get("me") or "Our Empire").split(" (")[0]
    civ = (game.get("me") or "").split("(")[-1].rstrip(")") or "the Empire"
    age = str(game.get("age") or "AGE_ANTIQUITY").replace("AGE_", "").title()
    use_premium = premium_mode and premium.available()
    notes = []

    def say(text):
        if use_premium:
            try:
                return premium.narrate(text, gemini_voice, leader, civ)
            except Exception as ex:
                notes.append(f"premium voice failed, used free voice: {ex}")
        return media.tts_mp3(text, voice)

    def wants_art(e):
        return use_premium and (illustrate == "all" or (illustrate == "headlines" and e.get("headline")))

    def wants_anim(e):
        return use_premium and (animate == "all" or (animate == "headlines" and e.get("headline")))

    def art(e):
        if style == "cinematic":  # photoreal still from the directed shot
            return premium.still(premium.shot_prompt(e.get("entry", ""), e.get("headline", ""), civ, age, style), style)
        return premium.illustrate(e.get("entry", ""), e.get("headline", ""), civ, age)

    scenes = {}

    def make_scene(e):
        try:
            if style == "painted":  # animate the painting itself, directed by the shot description
                img = premium.illustrate(e.get("entry", ""), e.get("headline", ""), civ, age)
                scenes[e["turn"]] = premium.scene(e.get("entry", ""), e.get("headline", ""), civ, age, style, image=img)
            else:
                scenes[e["turn"]] = premium.scene(e.get("entry", ""), e.get("headline", ""), civ, age, style)
        except Exception as ex:
            notes.append(f"animation failed for T{e['turn']}: {ex}")
    # fetch narration and paintings concurrently (network-bound), before rendering
    if use_premium:
        from concurrent.futures import ThreadPoolExecutor
        progress(2, "generating narration and paintings")
        with ThreadPoolExecutor(4) as ex:
            for e in entries:
                ex.submit(say, (e.get("headline") + ". " if e.get("headline") else "") + e.get("entry", ""))
                if wants_anim(e):
                    ex.submit(make_scene, e)
                elif wants_art(e):
                    ex.submit(lambda e=e: art(e))
    clips = []
    # title card
    title_txt = f"The Chronicle of {civ}"
    sub = f"as told by {leader}\nTurns {entries[0]['turn']}–{entries[-1]['turn']}"
    img = os.path.join(work, "title.jpg"); text_card({}, img, title=title_txt, subtitle=sub)
    aud = say(f"{title_txt}. As told by {leader}.")
    out = os.path.join(work, "c000.mp4"); make_clip(img, None, aud, out, pad=1.5, zoom=False); clips.append(out)
    for i, e in enumerate(entries, 1):
        progress(int(90 * i / len(entries)), f"turn {e['turn']} ({i}/{len(entries)})")
        text = (e.get("headline") + ". " if e.get("headline") else "") + e.get("entry", "")
        aud = say(text)
        frame = os.path.join(media.FRAMES, e["frame"]) if e.get("frame") else None
        if e["turn"] in scenes:
            cap = os.path.join(work, f"cap{i:03d}.png"); caption_layer(e, cap)
            make_anim_clip(scenes[e["turn"]][0], cap, aud, os.path.join(work, f"c{i:03d}.mp4"))
            clips.append(os.path.join(work, f"c{i:03d}.mp4"))
            continue
        if wants_art(e) or wants_anim(e):
            try:
                progress(int(90 * i / len(entries)), f"painting turn {e['turn']} ({i}/{len(entries)})")
                frame = art(e)
            except Exception as ex:
                notes.append(f"illustration failed for T{e['turn']}: {ex}")
        out = os.path.join(work, f"c{i:03d}.mp4")
        if frame and os.path.exists(frame):
            cap = os.path.join(work, f"cap{i:03d}.png"); caption_layer(e, cap)
            make_clip(frame, cap, aud, out)
        else:
            img = os.path.join(work, f"card{i:03d}.jpg"); text_card(e, img)
            make_clip(img, None, aud, out, zoom=False)
        clips.append(out)
    # end card
    ov = game
    sub = "\n".join(filter(None, [f"Turn {ov.get('turn')} · {str(ov.get('age', '')).replace('AGE_', '').title()} Age" if ov else "",
                                  f"{ov.get('cities')} settlements · science +{(ov.get('yields') or {}).get('sci')} · culture +{(ov.get('yields') or {}).get('cult')}" if ov else "",
                                  "The chronicle continues…"]))
    img = os.path.join(work, "end.jpg"); text_card({}, img, title="To be continued", subtitle=sub)
    aud = say("And so the chronicle continues.")
    out = os.path.join(work, "c999.mp4"); make_clip(img, None, aud, out, pad=2.0, zoom=False); clips.append(out)
    progress(93, "joining clips")
    lst = os.path.join(work, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{c.replace(os.sep, '/')}'\n" for c in clips)
    joined = os.path.join(work, "joined.mp4")
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    name = f"chronicle_T{entries[0]['turn']:03d}-T{entries[-1]['turn']:03d}_{time.strftime('%Y%m%d_%H%M')}.mp4"
    final = os.path.join(OUT_DIR, name)
    music = sorted(glob.glob(os.path.join(STATE, "music", "*.mp3")))
    if use_premium:  # original Lyria score matched to the chronicle's mood
        try:
            progress(94, "composing score")
            text_all = " ".join(e.get("entry", "").lower() for e in entries)
            martial = sum(text_all.count(w) for w in WAR_WORDS) > max(3, len(entries) // 3)
            mood = "tense and martial, war drums, determined" if martial else "stately, hopeful and grand, rising ambition"
            music = [premium.score(civ, age, mood)]
        except Exception as ex:
            notes.append(f"music failed: {ex}")
    if music:  # quiet background music under the narration
        progress(96, "mixing music")
        run([FFMPEG, "-y", "-loglevel", "error", "-i", joined, "-stream_loop", "-1", "-i", random.choice(music),
             "-filter_complex", "[1:a]volume=0.16,afade=t=in:d=3[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", final])
    else:
        os.replace(joined, final)
    shutil.rmtree(work, ignore_errors=True)
    progress(100, "done" + (f" ({len(notes)} fallbacks)" if notes else ""))
    if notes:
        with open(final[:-4] + ".notes.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(notes))
    return final


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="f", type=int, default=1)
    ap.add_argument("--to", dest="t", type=int, default=10 ** 6)
    ap.add_argument("--voice", default="en-GB-RyanNeural")
    ap.add_argument("--premium", action="store_true", help="Gemini voice + Lyria music + painted illustrations")
    ap.add_argument("--illustrate", default="headlines", choices=["headlines", "all", "none"])
    ap.add_argument("--gemini-voice", default="Charon")
    ap.add_argument("--animate", default="none", choices=["none", "headlines", "all"], help="Veo animated scenes")
    ap.add_argument("--style", default="painted", choices=["painted", "cinematic"])
    a = ap.parse_args()
    print(build(a.f, a.t, a.voice, lambda p, m: print(f"{p:3d}% {m}", flush=True), a.premium, a.illustrate, a.gemini_voice,
                a.animate, a.style))
