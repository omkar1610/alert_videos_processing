"""Combine per-video extractor output into clean, analysis-ready files.

Inputs : output/<video>/observations.csv, alerts.csv, time_anchor.txt
Outputs: output/all_observations.csv  every OCR reading + screen `state`
         output/all_alerts_raw.csv    extractor lifecycles, untouched
         output/all_alerts.csv        cleaned lifecycles (one row per alert handled)
         output/all_waiting.csv       intervals where the screen said "Waiting for alerts to arrive"

Screen states (for readings with no alert ID):
  WAITING        "Waiting for alerts to arrive"   -> queue empty, true idle
  INVESTIGATING  live camera view (GO LIVE / TODAY timeline) -> still working the alert
  OTHER          dialogs (Verify / Caveat), transitions, unreadable -> counted as busy

Cleaning of alert lifecycles:
  1. drop 1-frame OCR blips (<1 s): misread neighbour IDs or camera-watermark digits
  2. merge consecutive rows of the same ID when the screen never showed WAITING in between
     (the operator opened the camera view or a dialog and came back to the same alert);
     the time in between counts as handling time (`away_s`)
"""
import datetime as dt
import glob
import os

import pandas as pd

OUT = os.environ.get("OUT", "output")


# Columns of each output/<video>/alerts.csv (same as ALERT_COLS in extract.py)
ALERT_COLS = ["alert_id", "alert_name", "start_video_ts", "end_video_ts", "start_wall", "end_wall",
              "duration_s", "start_uncertainty_s", "end_uncertainty_s", "status", "n_samples",
              "min_conf", "flags"]

def hamming(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or len(a) != len(b):
        return 99
    return sum(x != y for x, y in zip(a, b))


def classify(row):
    if isinstance(row["alert_id"], str) and row["alert_id"]:
        return "ALERT"
    t = str(row["raw_text"]) if pd.notna(row["raw_text"]) else ""
    if "waiting for alerts" in t.lower():
        return "WAITING"
    if "GO LIVE" in t.upper() or "TODAY" in t.upper():
        return "INVESTIGATING"
    return "OTHER"


def anchor(vdir):
    for line in open(os.path.join(vdir, "time_anchor.txt")):
        if line.startswith("used_start="):
            return dt.datetime.fromisoformat(line.split("=", 1)[1].strip())


all_obs, all_raw, all_clean, all_wait = [], [], [], []
for vdir in sorted(glob.glob(os.path.join(OUT, "*", ""))):
    if not os.path.exists(os.path.join(vdir, "alerts.csv")):
        continue
    video = os.path.basename(os.path.dirname(vdir))
    start = anchor(vdir)

    def wall(ts):
        return (start + dt.timedelta(seconds=float(ts))).isoformat(timespec="milliseconds")

    o = pd.read_csv(os.path.join(vdir, "observations.csv"), dtype={"alert_id": str})
    o = o.sort_values("video_ts").reset_index(drop=True)
    o["state"] = o.apply(classify, axis=1)
    o.insert(0, "video", video)
    all_obs.append(o)

    # waiting intervals
    ts, st = o["video_ts"].to_numpy(), o["state"].to_numpy()
    i = 0
    while i < len(o):
        if st[i] == "WAITING":
            j = i
            while j + 1 < len(o) and st[j + 1] == "WAITING":
                j += 1
            end = ts[j + 1] if j + 1 < len(o) else ts[j]
            all_wait.append(dict(video=video, start_video_ts=ts[i], end_video_ts=end,
                                 start_wall=wall(ts[i]), end_wall=wall(end), duration_s=round(end - ts[i], 3)))
            i = j + 1
        else:
            i += 1

    a = pd.read_csv(os.path.join(vdir, "alerts.csv"), dtype={"alert_id": str})
    # Older runs wrote only an "alert_id" header when a recording had no alerts.
    a = a.reindex(columns=ALERT_COLS)
    if a.empty:
        print(f"note: no alerts found in {video} (its recorded and waiting time still count)")
        continue
    a["flags"] = a["flags"].fillna("")
    a.insert(0, "video", video)
    all_raw.append(a.copy())

    # 1. drop OCR blips
    ids = a["alert_id"].tolist()
    keep = []
    for k, r in a.iterrows():
        nb = [ids[k - 1] if k > 0 else None, ids[k + 1] if k + 1 < len(ids) else None]
        # one-frame readings under 1 s are OCR noise (a neighbour's ID misread, or digits from the
        # camera watermark "© Ambient.ai 2026-..."); real alerts last seconds over many frames
        blip = r["n_samples"] <= 1 and r["duration_s"] < 1.0 and (
            r["status"] == "removed" or any(0 < hamming(r["alert_id"], n) <= 2 for n in nb))
        keep.append(not blip)
    a = a[keep].reset_index(drop=True)

    # 2. merge same-ID rows with no WAITING in between
    rows = []
    for r in a.to_dict("records"):
        r["away_s"] = 0.0
        r["investigated"] = False
        if rows and rows[-1]["alert_id"] == r["alert_id"]:
            p = rows[-1]
            gap = o[(o["video_ts"] > p["end_video_ts"]) & (o["video_ts"] < r["start_video_ts"])]
            if not (gap["state"] == "WAITING").any():
                p["away_s"] += r["start_video_ts"] - p["end_video_ts"] + r["away_s"]
                p["investigated"] = p["investigated"] or (gap["state"] == "INVESTIGATING").any()
                for c in ["end_video_ts", "end_wall", "end_uncertainty_s", "status"]:
                    p[c] = r[c]
                p["n_samples"] += r["n_samples"]
                p["min_conf"] = min(float(p["min_conf"]), float(r["min_conf"]))
                p["flags"] = ";".join(sorted(set(filter(None, (p["flags"] + ";" + r["flags"]).split(";")))))
                p["merged_parts"] = p.get("merged_parts", 1) + 1
                continue
        r["merged_parts"] = 1
        rows.append(r)
    c = pd.DataFrame(rows)
    # investigation that happened without splitting the row (camera view < sampling gap) is rare; ignore
    c["duration_s"] = (c["end_video_ts"] - c["start_video_ts"]).round(3)
    c["start_wall"] = c["start_video_ts"].map(wall)
    c["end_wall"] = c["end_video_ts"].map(wall)
    # recompute flags that depend on the cleaned set
    def refl(r):
        f = [x for x in r["flags"].split(";") if x and x not in ("repeat_id", "short_lived", "single_sample")]
        if r["duration_s"] < 2:
            f.append("short_lived")
        if r["merged_parts"] > 1:
            f.append(f"merged_{r['merged_parts']}_parts")
        return f
    c["flags"] = c.apply(refl, axis=1)
    seen = {}
    for k in c.index:
        aid = c.at[k, "alert_id"]
        seen[aid] = seen.get(aid, 0) + 1
        if seen[aid] > 1:
            c.at[k, "flags"] = c.at[k, "flags"] + ["repeat_id(returned_to_queue)"]
    c["flags"] = c["flags"].map(";".join)
    c["away_s"] = c["away_s"].round(3)
    all_clean.append(c)

obs = pd.concat(all_obs, ignore_index=True).sort_values(["wall_time", "video", "video_ts"], kind="stable")
obs.to_csv(os.path.join(OUT, "all_observations.csv"), index=False)
(pd.concat(all_raw, ignore_index=True) if all_raw else pd.DataFrame(columns=["video"] + ALERT_COLS)).sort_values(["start_wall"]).to_csv(
    os.path.join(OUT, "all_alerts_raw.csv"), index=False)
cols = ["video", "alert_id", "alert_name", "start_wall", "end_wall", "duration_s", "start_video_ts",
        "end_video_ts", "start_uncertainty_s", "end_uncertainty_s", "status", "away_s", "investigated",
        "merged_parts", "n_samples", "min_conf", "flags"]
clean = (pd.concat(all_clean, ignore_index=True) if all_clean else pd.DataFrame(columns=cols)).sort_values(["start_wall"])[cols]
clean.to_csv(os.path.join(OUT, "all_alerts.csv"), index=False)
wait = pd.DataFrame(all_wait)
wait.to_csv(os.path.join(OUT, "all_waiting.csv"), index=False)

print(f"observations: {len(obs)}  states: {obs['state'].value_counts().to_dict()}")
print(f"alerts raw: {sum(len(x) for x in all_raw)}  -> clean: {len(clean)}")
print(f"waiting intervals: {len(wait)}  total {wait['duration_s'].sum() if len(wait) else 0:.0f}s")
