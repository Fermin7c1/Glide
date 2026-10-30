#!/usr/bin/env python3
"""The YouTube clips RestedXP links from its steps ("CLICK HERE for an example"), fetched and cut into stills so
each can be read and turned into a short spoken instruction for that step (knowledge/step_videos.json).

    python tools/step_videos.py list                 # every clip and the steps that link it
    python tools/step_videos.py fetch [--only ID,..]  # metadata, captions, the clip (or the linked part of a long video)
    python tools/step_videos.py frames [--only ...]   # contact sheets of stills (one per second) + captions for that part
    python tools/step_videos.py merge --results DIR   # readings (one JSON per batch) -> knowledge/step_videos.json
    python tools/step_videos.py clean                 # delete data/videos once the instructions are written

Everything fetched stays on this PC in data/videos/<video id>/ (gitignored): the video, info.json, captions.txt,
sheet-NN.jpg. Only the instructions written from them (knowledge/step_videos.json) belong to Glide.
Needs yt-dlp and ffmpeg (winget install yt-dlp.yt-dlp yt-dlp.FFmpeg); found on PATH or in winget's package folder.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "data", "videos")
KB = os.path.join(ROOT, "data", "kb.db")
WHOLE_MAX_S = 600            # clips up to 10 minutes are taken whole
PART_S = 180                 # a link into a longer video: the chapter it falls in, else this long from the timestamp
SHEET_EVERY_S = 1            # one still every second (jumps, ledges and kiting need it), 3 x 3 per contact sheet


def log(*a):
    print("[videos]", *a, flush=True)


if hasattr(sys.stdout, "reconfigure"):          # video titles in any script; a Windows console is cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def tool(name):
    """yt-dlp / ffmpeg: on PATH, else in winget's package folder."""
    p = shutil.which(name)
    if p:
        return p
    pkgs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    hits = glob.glob(os.path.join(pkgs, "yt-dlp.*", "**", name + ".exe"), recursive=True)
    if not hits:
        raise SystemExit("%s not found: winget install yt-dlp.yt-dlp yt-dlp.FFmpeg" % name)
    return hits[0]


def parse_url(url):
    """-> (video id, start seconds or 0) for youtube.com/watch, youtu.be, shorts, embed and live links."""
    u = urlparse(url)
    q = parse_qs(u.query)
    if "youtu.be" in u.netloc:
        vid = u.path.strip("/").split("/")[0]
    elif "/watch" in u.path:
        vid = (q.get("v") or [""])[0]
    else:
        m = re.search(r"/(?:shorts|embed|live)/([\w-]{6,})", u.path)
        vid = m.group(1) if m else ""
    t = (q.get("t") or q.get("start") or ["0"])[0]
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", t or "0")
    start = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0
    return vid, start


def clips():
    """{clip key "<id>@<start>": {"id", "start", "url", "steps": [{"guide", "idx", "label", "step"}]}} from kb.db."""
    db = sqlite3.connect(KB)
    out = {}
    rows = db.execute("""SELECT a.guide_key, a.idx, a.args, a.text, s.text FROM rxp_action a
                         LEFT JOIN rxp_step s ON s.guide_key = a.guide_key AND s.idx = a.idx
                         WHERE a.verb = 'link' ORDER BY a.guide_key, a.idx""")
    for guide, idx, args, label, step in rows:
        try:
            url = json.loads(args).get("url") if args and args.startswith("{") else args
        except ValueError:
            url = None
        if not url or not re.search(r"youtu\.?be", url, re.I):
            continue
        vid, start = parse_url(url)
        if not vid:
            continue
        c = out.setdefault("%s@%d" % (vid, start), {"id": vid, "start": start, "url": url, "steps": []})
        c["steps"].append({"guide": guide, "idx": idx, "label": label, "step": step})
    return out


def _dir(c):
    return os.path.join(OUT, c["id"])


def part(info, start):
    """(from, to) seconds of the video to keep for a link starting at `start`."""
    dur = info.get("duration") or 0
    if dur and dur <= WHOLE_MAX_S and not start:
        return 0, dur
    # from just before the link on; chapters don't cut it short (a link near a chapter's end runs on into the next)
    end = start + PART_S
    for ch in info.get("chapters") or []:
        if ch["start_time"] <= start < ch["end_time"]:
            end = max(end, min(ch["end_time"], start + 2 * PART_S))
    return max(0, start - 10), min(dur or end, end)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def fetch_video(vid, cs):
    """One video and all its linked parts: metadata and captions, the video downloaded whole at full speed (8
    fragments at a time), each linked part cut out locally, the whole file deleted. -> (ok, line for the log)"""
    d = os.path.join(OUT, vid)
    os.makedirs(d, exist_ok=True)
    ytdlp, ffmpeg = tool("yt-dlp"), tool("ffmpeg")
    url = "https://www.youtube.com/watch?v=" + vid
    info_path = os.path.join(d, "info.json")
    if not os.path.exists(info_path):
        base = [ytdlp, "--skip-download", "--write-info-json", "-o", os.path.join(d, "v.%(ext)s")]
        r = _run(base + ["--write-auto-subs", "--write-subs", "--sub-langs", "en.*", "--sub-format", "vtt",
                         "--sleep-subtitles", "2", url])
        if r.returncode:            # captions are a bonus: YouTube rate-limits them (HTTP 429); carry on without
            r = _run(base + [url])
        if r.returncode:
            return False, "%s metadata failed: %s" % (vid, (r.stderr or "").strip().splitlines()[-1:])
        os.replace(os.path.join(d, "v.info.json"), info_path)
    info = json.load(open(info_path, encoding="utf-8"))
    parts = sorted({part(info, c["start"]) for c in cs})
    want = [(a, b) for a, b in parts if not os.path.exists(os.path.join(d, "clip-%d-%d.mp4" % (a, b)))]
    if not want:
        return True, "%s already here" % vid
    full = os.path.join(d, "full.mp4")
    if not os.path.exists(full):
        r = _run([ytdlp, "-N", "8", "-f", "bv*[height<=480]+ba/b[height<=480]/b", "--merge-output-format", "mp4",
                  "--ffmpeg-location", os.path.dirname(ffmpeg), "--no-part", "-o", full, url])
        if r.returncode or not os.path.exists(full):
            return False, "%s download failed: %s" % (vid, (r.stderr or "").strip().splitlines()[-1:])
    for a, b in want:
        if (a, b) == (0, info.get("duration")):
            shutil.copyfile(full, os.path.join(d, "clip-%d-%d.mp4" % (a, b)))
            continue
        r = _run([ffmpeg, "-loglevel", "error", "-y", "-ss", str(a), "-to", str(b), "-i", full,
                  "-c", "copy", os.path.join(d, "clip-%d-%d.mp4" % (a, b))])
        if r.returncode:
            return False, "%s cut %d-%d failed: %s" % (vid, a, b, (r.stderr or "").strip()[-200:])
    os.remove(full)                     # the whole video was only needed to cut the parts from
    return True, "%s: %s (%s)" % (vid, info.get("title"), ", ".join("%d-%d s" % p for p in want))


def fetch_all(cs, workers=8):
    """Every video, `workers` at a time, one log line per video as it finishes. -> number done"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    by_video = {}
    for c in cs:
        by_video.setdefault(c["id"], []).append(c)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(fetch_video, vid, group): vid for vid, group in by_video.items()}
        for n, f in enumerate(as_completed(jobs), 1):
            try:
                ok, line = f.result()
            except Exception as e:  # noqa: BLE001 - one bad video doesn't stop the rest
                ok, line = False, "%s failed: %s" % (jobs[f], e)
            done += ok
            log("%d/%d %s %s" % (n, len(jobs), "ok  " if ok else "FAIL", line))
    return done, len(by_video)


def vtt_text(path, a, b):
    """Plain caption lines between a and b seconds (auto-captions repeat lines; consecutive repeats dropped)."""
    out, t0 = [], None
    for ln in open(path, encoding="utf-8", errors="replace").read().splitlines():
        m = re.match(r"(\d+):(\d+):(\d+)\.\d+ -->", ln)
        if m:
            t0 = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            continue
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if t0 is None or not ln or ln.startswith(("WEBVTT", "Kind:", "Language:")) or not a <= t0 <= b:
            continue
        if not out or out[-1][1] != ln:
            out.append((t0, ln))
    return "\n".join("%d:%02d %s" % (t // 60, t % 60, s) for t, s in out)


def frames(c):
    """Contact sheets for one linked part: a still every SHEET_EVERY_S seconds stamped with its time in the video,
    3 x 3 per sheet, and the captions for the same span."""
    d = _dir(c)
    info_path = os.path.join(d, "info.json")
    if not os.path.exists(info_path):
        return False
    a, b = part(json.load(open(info_path, encoding="utf-8")), c["start"])
    clip = os.path.join(d, "clip-%d-%d.mp4" % (a, b))
    if not os.path.exists(clip):
        return False
    tag = "%d-%d" % (a, b)
    rate = "every%ds" % SHEET_EVERY_S
    if glob.glob(os.path.join(d, "sheet-%s-%s-*.jpg" % (tag, rate))):
        return True
    for old in glob.glob(os.path.join(d, "sheet-%s-*.jpg" % tag)):     # this part's sheets at another rate
        os.remove(old)
    ffmpeg = tool("ffmpeg")
    vf = ("fps=1/%d,scale=640:-2,drawtext=text='%%{pts\\:hms\\:%d}':x=8:y=8:fontsize=22:fontcolor=white:"
          "box=1:boxcolor=black@0.6,tile=3x3:padding=4" % (SHEET_EVERY_S, a))
    r = _run([ffmpeg, "-loglevel", "error", "-i", clip, "-vf", vf, "-q:v", "4",
              os.path.join(d, "sheet-%s-%s-%%03d.jpg" % (tag, rate))])
    if r.returncode:
        log(c["id"], "frames failed:", (r.stderr or "").strip()[-300:])
        return False
    subs = sorted(glob.glob(os.path.join(d, "v.en*.vtt")))
    with open(os.path.join(d, "captions-%s.txt" % tag), "w", encoding="utf-8") as fh:
        fh.write(vtt_text(subs[0], a, b) if subs else "")
    return True


NOTES = os.path.join(ROOT, "knowledge", "step_videos.json")
SPOKEN_MAX, DETAIL_MAX = 300, 1200


def copied(text, captions, n=8):
    """Does `text` repeat `n` or more consecutive words of the captions? (The notes are written in our own words.)"""
    words = lambda t: re.findall(r"[a-z0-9']+", (t or "").lower())  # noqa: E731
    cap = words(captions)
    grams = {tuple(cap[i:i + n]) for i in range(len(cap) - n + 1)}
    w = words(text)
    return any(tuple(w[i:i + n]) in grams for i in range(len(w) - n + 1))


def merge(results_dir):
    """Readings -> knowledge/step_videos.json: {"clips": {key: {url, steps, instruction, detail, confidence, skills,
    mechanics}}}. Keeps the limits, drops the readers' internal notes, refuses text copied from the captions."""
    cs = clips()
    out, problems = {}, []
    for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        for key, r in json.load(open(path, encoding="utf-8")).items():
            c = cs.get(key)
            if not c:
                problems.append("%s: not a linked clip" % key)
                continue
            ins = " ".join((r.get("instruction") or "").split())
            det = " ".join((r.get("detail") or "").split())
            conf = r.get("confidence")
            if conf not in ("high", "medium", "low") or not ins:
                problems.append("%s: no instruction or confidence" % key)
                continue
            if len(ins) > SPOKEN_MAX or len(det) > DETAIL_MAX:
                problems.append("%s: too long (%d / %d chars)" % (key, len(ins), len(det)))
            caps = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(OUT, c["id"], "captions-*.txt")))
            if copied(ins + " " + det, caps):
                problems.append("%s: repeats the captions word for word" % key)
                continue
            out[key] = {"url": c["url"], "steps": [{"guide": s_["guide"], "idx": s_["idx"]} for s_ in c["steps"]],
                        "instruction": ins, "detail": det, "confidence": conf,
                        "skills": r.get("skills") or [], "mechanics": r.get("mechanics") or []}
    doc = {"_about": "What each YouTube clip RestedXP links from a step shows, in our own words (tools/step_videos.py; "
                     "companion/briefing.py says the instruction when the player reaches the step). Low-confidence "
                     "notes are kept for reference but never said.",
           "clips": dict(sorted(out.items()))}
    with open(NOTES, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    counts = {k: sum(1 for v in out.values() if v["confidence"] == k) for k in ("high", "medium", "low")}
    log("%d notes -> knowledge/step_videos.json %s; %d of %d linked clips have none" % (
        len(out), counts, len(cs) - len(out), len(cs)))
    for p_ in problems:
        log("  problem:", p_)
    return out, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stage", choices=("list", "fetch", "frames", "merge", "clean"))
    ap.add_argument("--only", default="", help="comma-separated video ids")
    ap.add_argument("--workers", type=int, default=8, help="videos fetched (or cut into stills) at the same time")
    ap.add_argument("--results", default="", help="merge: the folder of reading results (one JSON per batch)")
    a = ap.parse_args(argv)
    cs = clips()
    only = set(filter(None, a.only.split(",")))
    todo = [c for c in cs.values() if not only or c["id"] in only]
    if a.stage == "merge":
        _, problems = merge(a.results)
        return 1 if problems else 0
    if a.stage == "clean":           # videos, stills and captions are only working material
        size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs) if os.path.isdir(OUT) else 0
        shutil.rmtree(OUT, ignore_errors=True)
        log("deleted data/videos (%.1f MB)" % (size / 1e6))
        return 0
    if a.stage == "list":
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, "clips.json"), "w", encoding="utf-8") as fh:
            json.dump(cs, fh, indent=1, ensure_ascii=False)
        log("%d clips (%d videos) linked from %d steps -> data/videos/clips.json"
            % (len(cs), len({c["id"] for c in cs.values()}), sum(len(c["steps"]) for c in cs.values())))
        return 0
    if a.stage == "fetch":
        ok, n = fetch_all(todo, a.workers)
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            ok, n = sum(pool.map(frames, todo)), len(todo)
    log("%s: %d of %d done" % (a.stage, ok, n))
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
