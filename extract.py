"""Alert lifecycle extractor for Mac screen recordings.

Wall time = recording start (from the macOS file name) + video presentation timestamp.
Alert ID = the 8-10 digit number on the alert panel line ("<Alert Name> • 489890272"),
read with Apple Vision OCR from a wide centre band (the panel moves around).

Single pass per video:
  * every frame is decoded; a cropped, half-res copy is buffered
  * every 1/fps seconds the current crop is OCR'd
  * when the reading changes between two samples, the buffered frames in between are
    binary-searched with OCR to find the exact transition frame

--fast: before each OCR call, the alert ID line (where the ID was last read) is compared
pixel by pixel with the frame that was last OCR'd. Unchanged pixels = same reading, so
OCR is skipped; the exact transition frame is found by the same pixel comparison and
confirmed with one OCR call. Same results, far fewer OCR calls.

Outputs (output/<video stem>/):
  observations.csv  every OCR call (sampled + refinement), raw text kept
  alerts.csv        one row per alert lifecycle: start / end (video ts + wall time)

Usage:
  .venv/bin/python extract.py "Screen Recording ... .mov" [more files] [--fps 2] [--start "2026-11-06 08:44:08"]
"""
import argparse
import csv
import datetime as dt
import os
import re
import sys
import time
from multiprocessing import Pool

import cv2
from PIL import Image
from ocrmac import ocrmac

# Crop band (fractions of frame) that always contains the alert info panel.
Y0, Y1, X0, X1 = 0.50, 0.97, 0.12, 0.83
SCALE = 0.5
# Alert ID = exactly 9 digits right after the bullet ("Person Fallen Down • 489890743").
# Strict on purpose: the camera feed's burned-in watermark ("© Ambient.ai 2026-11-05 ...")
# and camera/site IDs ("• 49052") also produce digit runs.
# A 10th digit right after the ID is the copy icon misread as "0" -> keep the first 9.
ID_RE = re.compile(r"[•●·]\s*(\d{9})\d?(?!\d)")
ID_NOBULLET_RE = re.compile(r"[A-Za-z\]\)]\s+(\d{9})\d?(?!\d)")  # bullet dropped by OCR
ID_ONLY_RE = re.compile(r"^\s*[•●·]?\s*(\d{9})\d?\s*$")  # box containing just the ID
# A NONE gap shorter than this between two readings of the same ID is treated as an
# OCR dropout / overlay, not a removal.
GAP_MERGE_S = 2.0

FNAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})\s*([AP]M)", re.I)


def start_from_filename(path):
    m = FNAME_RE.search(os.path.basename(path).replace(" ", " "))
    if not m:
        return None
    d, h, mi, s, ap = m.groups()
    h = int(h) % 12 + (12 if ap.upper() == "PM" else 0)
    return dt.datetime.fromisoformat(d).replace(hour=h, minute=int(mi), second=int(s))


def crop(frame):
    H, W = frame.shape[:2]
    c = frame[int(H * Y0):int(H * Y1), int(W * X0):int(W * X1)]
    return cv2.resize(c, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)


def read_id(img):
    """Return (alert_id or None, alert_name, confidence, raw_text, n_distinct_ids)."""
    return read_id_box(img)[:5]


# --fast: pixels counted as changed when they differ by more than this (0-255) ...
PIX_DIFF = 40
# ... and the ID line counts as changed when more than this many pixels did.
# Measured: same alert 0 pixels (rarely ~150 for a hover), next alert 930+.
PIX_CHANGED = 20


def id_band(img, box):
    """Pixel rectangle (y0, y1, x0, x1) around an OCR text box, with a small margin."""
    H, W = img.shape[:2]
    x, y, w, h = box
    y0 = int((1 - y - h) * H - .3 * h * H)
    y1 = int((1 - y) * H + .3 * h * H)
    return max(0, y0), min(H, y1), max(0, int(x * W) - 10), min(W, int((x + w) * W) + 10)


def band_pixels(img, rect):
    y0, y1, x0, x1 = rect
    return cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)


def band_changed(img, anchor):
    return int((cv2.absdiff(band_pixels(img, anchor["rect"]), anchor["pix"]) > PIX_DIFF).sum()) > PIX_CHANGED


def read_id_box(img):
    """read_id plus the pixel rectangle of the chosen ID line (None when no ID)."""
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    res = ocrmac.OCR(im, recognition_level="accurate", language_preference=["en-US"]).recognize()
    hits = []
    for text, conf, box in res:
        ms = (list(ID_RE.finditer(text)) or list(ID_NOBULLET_RE.finditer(text))
              or list(ID_ONLY_RE.finditer(text)))
        for m in ms:
            # box = (x, y, w, h) normalised, origin bottom-left -> larger y is higher up
            name = re.split(r"\s*[•●·]\s*", text[:m.start()].strip())[0].strip(" •●·")
            hits.append((box[1] + box[3], m.group(1), name, conf, text, box))
    if not hits:
        return None, "", 0.0, " | ".join(t for t, _, _ in res)[:300], 0, None
    hits.sort(key=lambda h: h[:5], reverse=True)  # topmost line = alert ID line (same order as before)
    _, aid, name, conf, text, box = hits[0]
    return aid, name, float(conf), text, len({h[1] for h in hits}), id_band(img, box)


CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})")


def read_clock(img):
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    txt = " ".join(r[0] for r in ocrmac.OCR(im).recognize())
    m = CLOCK_RE.search(txt)
    return (int(m.group(1)), int(m.group(2))) if m else None


def calibrate_start(path, fname_start, max_s=70.0):
    """Find the exact frame where the menu-bar minute flips; that frame is HH:MM:00.
    Returns (start_datetime, note). Falls back to the file-name time."""
    if fname_start is None:
        return None, "no start time"
    cap = cv2.VideoCapture(path)
    frames, first = [], None  # (ts, clock_crop)
    while cap.grab():
        ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        first = ts if first is None else first
        ts -= first
        if ts > max_s:
            break
        ok, f = cap.retrieve()
        H, W = f.shape[:2]
        frames.append((ts, f[0:int(H * .035), int(W * .80):W].copy()))
    cap.release()
    cache = {}

    def clk(i):
        if i not in cache:
            cache[i] = read_clock(frames[i][1])
        return cache[i]

    step = max(1, len(frames) // 140)  # ~0.5 s
    idxs = list(range(0, len(frames), step))
    base = None
    for a, b in zip(idxs, idxs[1:]):
        if base is None:
            base = clk(a)
        cb = clk(b)
        if base is None or cb is None or cb == base:
            continue
        lo, hi = a, b
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if clk(mid) == base:
                lo = mid
            else:
                hi = mid
        h, m = cb
        expected = fname_start + dt.timedelta(seconds=frames[hi][0])
        flip = (expected + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)
        if flip.minute != m:
            break  # clock reading doesn't match the file-name estimate; fall back
        start = flip - dt.timedelta(seconds=frames[hi][0])
        diff = (start - fname_start).total_seconds()
        if abs(diff) > 5:
            return fname_start, f"clock anchor disagreed with file name by {diff:.1f}s; used file name"
        return start, f"clock flip to {h}:{m:02d} at video t={frames[hi][0]:.3f}s; file-name offset {diff:+.3f}s"
    return fname_start, "no clock flip found in first 70s; used file-name time (±1s)"


def best_name(names):
    names = [n for n in names if len(n) > 3 and re.search(r"[A-Za-z]{3}", n)]
    return max(set(names), key=names.count) if names else ""


def process(args):
    path, fps, start_override, outroot, fast = args
    stem = os.path.splitext(os.path.basename(path))[0]
    outdir = os.path.join(outroot, stem)
    os.makedirs(outdir, exist_ok=True)
    if os.path.exists(os.path.join(outdir, "alerts.csv")):
        return f"skip (done): {stem}"

    if start_override:
        start, note = dt.datetime.fromisoformat(start_override), "override"
    else:
        start, note = calibrate_start(path, start_from_filename(path))
    with open(os.path.join(outdir, "time_anchor.txt"), "w") as fh:
        fh.write(f"file_name_start={start_from_filename(path)}\nused_start={start}\n{note}\n")
    print(f"[{stem[:40]}] start={start}  ({note})", flush=True)

    def wall(ts):
        return (start + dt.timedelta(seconds=ts)).isoformat(timespec="milliseconds") if start else ""

    cap = cv2.VideoCapture(path)
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1
    obs_f = open(os.path.join(outdir, "observations.csv"), "w", newline="")
    ow = csv.writer(obs_f)
    ow.writerow(["frame", "video_ts", "wall_time", "kind", "alert_id", "alert_name",
                 "ocr_conf", "n_ids_in_frame", "raw_text"])

    samples = []   # (frame_idx, ts, alert_id, name, conf)
    cache = {}     # frame_idx -> OCR result (per transition search)
    anchor = None  # --fast: last OCR'd ID line: dict(rect, pix, res)
    n_ocr = n_reused = 0

    def ocr_at(idx, ts, img, kind):
        nonlocal anchor, n_ocr, n_reused
        if idx in cache:
            return cache[idx]
        if fast and anchor is not None and not band_changed(img, anchor):
            res = anchor["res"]          # ID line pixels unchanged -> same reading
            n_reused += 1
        else:
            *res, rect = read_id_box(img)
            n_ocr += 1
            # Only regular samples set the reference. Frames read during a transition
            # search can be mid-animation (e.g. a dialog fading in over the panel) and
            # must not be reused for later frames.
            if kind == "sample":
                anchor = dict(rect=rect, pix=band_pixels(img, rect), res=res) if rect else None
        aid, name, conf, text, nids = res
        ow.writerow([idx, f"{ts:.3f}", wall(ts), kind, aid or "", name, f"{conf:.2f}", nids, text])
        cache[idx] = (aid, name, conf)
        return cache[idx]

    buf = []       # [(frame_idx, ts, crop)] frames after the previous sample
    prev = None    # (frame_idx, ts, img, aid)
    first_pts = None
    next_t = 0.0
    idx = -1
    t_start = time.time()
    transitions = []  # (last_idx_old, ts_old, first_idx_new, ts_new)

    while cap.grab():
        idx += 1
        ok, f = cap.retrieve()
        if not ok:
            continue
        pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if first_pts is None:
            first_pts = pts
        ts = pts - first_pts
        img = crop(f)
        if ts + 1e-6 < next_t:
            buf.append((idx, ts, img))
            continue
        next_t = ts + 1.0 / fps
        cache.clear()
        prev_anchor = anchor
        aid, name, conf = ocr_at(idx, ts, img, "sample")
        if prev is not None and aid != prev[3]:
            # binary search in buf for first frame whose reading != prev reading
            lo_i, hi_i = -1, len(buf)  # buf[-1]=prev, buf[len]=current
            if fast and prev_anchor is not None and prev_anchor["res"][0] == prev[3]:
                # pixels can only change at or before the reading changes: find the first
                # frame whose ID line differs (no OCR), then confirm it with one OCR call
                p_lo, p_hi = -1, len(buf)
                while p_hi - p_lo > 1:
                    mid = (p_lo + p_hi) // 2
                    if band_changed(buf[mid][2], prev_anchor):
                        p_hi = mid
                    else:
                        p_lo = mid
                lo_i = p_lo          # frames up to here look identical -> old reading
                if p_hi < len(buf):
                    fi, fts, fim = buf[p_hi]
                    if ocr_at(fi, fts, fim, "refine")[0] != prev[3]:
                        hi_i = p_hi  # first changed frame already reads differently
                    else:
                        lo_i = p_hi  # changed pixels, same ID: keep searching with OCR
            while hi_i - lo_i > 1:
                mid = (lo_i + hi_i) // 2
                fi, fts, fim = buf[mid]
                r = ocr_at(fi, fts, fim, "refine")
                if r[0] == prev[3]:
                    lo_i = mid
                else:
                    hi_i = mid
            last_old = (prev[0], prev[1]) if lo_i == -1 else (buf[lo_i][0], buf[lo_i][1])
            first_new = (idx, ts) if hi_i == len(buf) else (buf[hi_i][0], buf[hi_i][1])
            transitions.append((prev[3], aid, last_old, first_new))
        samples.append((idx, ts, aid, name, conf))
        prev = (idx, ts, img, aid)
        buf = []
        if len(samples) % 200 == 0:
            obs_f.flush()
            el = time.time() - t_start
            print(f"[{stem[:40]}] {idx/total*100:5.1f}%  t={ts:7.1f}s  {el:5.0f}s elapsed", flush=True)

    end_ts = samples[-1][1] if samples else 0.0
    obs_f.close()
    cap.release()

    # ---- build lifecycles from samples + refined transitions -----------------------
    # segments: maximal runs of equal reading (None = no ID visible / unreadable)
    segs = []  # dict(aid, name, start_lo, start_hi, end_lo, end_hi, n, confs)
    t_iter = iter(transitions)
    for i, (fi, ts, aid, name, conf) in enumerate(samples):
        if segs and segs[-1]["aid"] == aid:
            s = segs[-1]
            s["n"] += 1
            s["confs"].append(conf)
            s["end_lo"] = ts
            s["names"].append(name)
            continue
        if segs:
            old, new, lo, hi = next(t_iter)
            segs[-1]["end_lo"] = lo[1]   # last frame still showing old reading
            segs[-1]["end_hi"] = hi[1]   # first frame showing new reading
            st_lo, st_hi = lo[1], hi[1]
        else:
            st_lo, st_hi = ts, ts
        segs.append(dict(aid=aid, name=name, start_lo=st_lo, start_hi=st_hi,
                         end_lo=ts, end_hi=None, n=1, confs=[conf], names=[name]))

    # merge short gaps (no ID, or a different misread ID) sandwiched between the same ID:
    # OCR dropouts, dialogs half-covering the panel, etc.
    # Any run of segments totalling < GAP_MERGE_S between two readings of the same ID
    # is noise and is folded into that ID.
    merged = []
    for s in segs:
        if s["aid"] is not None:
            j = len(merged) - 1
            while j >= 0 and merged[j]["aid"] != s["aid"]:
                j -= 1
            if j >= 0 and j < len(merged) - 1 and s["start_hi"] - merged[j]["end_lo"] < GAP_MERGE_S:
                a = merged[j]
                n_gap = len(merged) - 1 - j
                del merged[j + 1:]
                a.update(end_lo=s["end_lo"], end_hi=s["end_hi"], n=a["n"] + s["n"],
                         confs=a["confs"] + s["confs"], names=a["names"] + s["names"])
                a["gaps"] = a.get("gaps", 0) + n_gap
                continue
        merged.append(s)

    seen = {}
    rows = []
    for s in merged:
        if s["aid"] is None:
            continue
        seen[s["aid"]] = seen.get(s["aid"], 0) + 1
        still = s["end_hi"] is None
        end_ts_best = s["end_lo"] if still else s["end_hi"]
        start_ts_best = s["start_hi"]
        flags = []
        if len(s["aid"]) != 9:
            flags.append("id_len")
        if s["n"] <= 1:
            flags.append("single_sample")
        if (s["end_hi"] or s["end_lo"]) - s["start_hi"] < GAP_MERGE_S:
            flags.append("short_lived")
        if min(s["confs"]) < 0.8:
            flags.append("low_conf")
        if seen[s["aid"]] > 1:
            flags.append("repeat_id")
        if s.get("gaps"):
            flags.append(f"ocr_gaps={s['gaps']}")
        if s["start_lo"] == 0 and s["start_hi"] == 0:
            flags.append("visible_at_start")
        rows.append({
            "alert_id": s["aid"],
            "alert_name": best_name(s["names"]),
            "start_video_ts": f"{start_ts_best:.3f}",
            "end_video_ts": f"{end_ts_best:.3f}",
            "start_wall": wall(start_ts_best),
            "end_wall": wall(end_ts_best),
            "duration_s": f"{end_ts_best - start_ts_best:.3f}",
            "start_uncertainty_s": f"{s['start_hi'] - s['start_lo']:.3f}",
            "end_uncertainty_s": "" if still else f"{s['end_hi'] - s['end_lo']:.3f}",
            "status": "still_visible_at_end" if still else "removed",
            "n_samples": s["n"],
            "min_conf": f"{min(s['confs']):.2f}",
            "flags": ";".join(flags),
        })

    # alerts.csv is written last and atomically: its presence means this video finished
    # (run_all.py relies on that to skip it and to delete the recording).
    tmp = os.path.join(outdir, "alerts.csv.tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["alert_id"])
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, os.path.join(outdir, "alerts.csv"))
    extra = f"  ocr_calls={n_ocr} skipped={n_reused}" if fast else f"  ocr_calls={n_ocr}"
    return (f"done: {stem}  start={start}  video={end_ts:.0f}s  alerts={len(rows)}  "
            f"elapsed={time.time()-t_start:.0f}s" + extra)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--fps", type=float, default=2.0, help="OCR sampling rate (refined to exact frame)")
    ap.add_argument("--start", default=None, help="override recording start 'YYYY-MM-DD HH:MM:SS'")
    ap.add_argument("--out", default="output")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--fast", action="store_true",
                    help="skip OCR when the alert ID line's pixels haven't changed (same results, much faster)")
    a = ap.parse_args()
    jobs = [(v, a.fps, a.start, a.out, a.fast) for v in a.videos]
    if len(jobs) == 1:
        print(process(jobs[0]))
    else:
        with Pool(min(a.workers, len(jobs))) as p:
            for r in p.imap_unordered(process, jobs):
                print(r, flush=True)


if __name__ == "__main__":
    sys.exit(main())
