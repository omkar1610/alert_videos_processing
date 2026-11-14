"""Productivity analysis on output/all_alerts.csv + output/all_observations.csv.

Writes to output/analysis/:
  buckets_1min.csv, buckets_5min.csv, buckets_15min.csv
  by_alert_type.csv
  session_summary.csv   (per recording + overall)

Definitions
  covered_s        seconds of this bucket that were actually recorded (gaps between recordings excluded)
  idle_s           seconds the portal showed "Waiting for alerts to arrive" (queue empty)
  busy_s           covered_s - idle_s (alert on screen, camera investigation, dialogs)
  cleared          alerts whose removal happened in this bucket
  arrived          alerts that first appeared in this bucket
  avg/median_handle_s   duration of alerts cleared in this bucket (alerts already on screen when a
                   recording started are excluded: their real start is unknown)
  clears_per_busy_min   cleared / (busy_s/60)  -> speed while there was work, independent of queue volume
  next_alert_gap_s gap between an alert's removal and the next alert appearing (0 = queue backed up)
"""
import os

import numpy as np
import pandas as pd

OUT = os.environ.get("OUT", "output")
AOUT = os.path.join(OUT, "analysis")
os.makedirs(AOUT, exist_ok=True)

al = pd.read_csv(os.path.join(OUT, "all_alerts.csv"), dtype={"alert_id": str},
                 parse_dates=["start_wall", "end_wall"])
obs = pd.read_csv(os.path.join(OUT, "all_observations.csv"), usecols=["video", "wall_time"],
                  parse_dates=["wall_time"])
al["flags"] = al["flags"].fillna("")
wt = pd.read_csv(os.path.join(OUT, "all_waiting.csv"), parse_dates=["start_wall", "end_wall"])
al["duration_s"] = al["duration_s"].astype(float)
al["truncated_start"] = al["flags"].str.contains("visible_at_start")
al["removed"] = al["status"].eq("removed")
al["handle_valid"] = al["removed"] & ~al["truncated_start"]

# recording coverage per video
cov = obs.groupby("video")["wall_time"].agg(["min", "max"]).reset_index()
# (all_observations.csv only holds fully processed videos; ones with no alerts are kept)

# gap to next alert (same video only)
al = al.sort_values(["video", "start_wall"]).reset_index(drop=True)
nxt = al.groupby("video")["start_wall"].shift(-1)
al["next_alert_gap_s"] = np.where(al["removed"], (nxt - al["end_wall"]).dt.total_seconds(), np.nan)
al["next_alert_gap_s"] = al["next_alert_gap_s"].clip(lower=0)


def overlap(a0, a1, b0, b1):
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


def buckets(freq):
    rows = []
    for _, c in cov.iterrows():
        v_al = al[al["video"] == c["video"]]
        v_wt = wt[wt["video"] == c["video"]]
        for b0 in pd.date_range(c["min"].floor(freq), c["max"], freq=freq):
            b1 = b0 + pd.Timedelta(freq)
            covered = overlap(b0, b1, c["min"], c["max"])
            if covered <= 0:
                continue
            idle = sum(overlap(b0, b1, s, e) for s, e in zip(v_wt["start_wall"], v_wt["end_wall"]))
            busy = covered - idle
            cl = v_al[v_al["removed"] & (v_al["end_wall"] >= b0) & (v_al["end_wall"] < b1)]
            hv = cl[cl["handle_valid"]]["duration_s"]
            arr = v_al[(v_al["start_wall"] >= b0) & (v_al["start_wall"] < b1) & ~v_al["truncated_start"]]
            rows.append(dict(video=c["video"], bucket_start=b0, covered_s=covered, busy_s=busy, cleared=len(cl),
                             arrived=len(arr), investigated=int(cl["investigated"].sum()), hv=list(hv), gaps=list(cl["next_alert_gap_s"].dropna())))
    df = pd.DataFrame(rows)
    # merge buckets that span two videos (shouldn't normally happen, but be safe)
    g = df.groupby("bucket_start").agg(
        covered_s=("covered_s", "sum"), busy_s=("busy_s", "sum"), cleared=("cleared", "sum"),
        arrived=("arrived", "sum"), investigated=("investigated", "sum"), hv=("hv", "sum"), gaps=("gaps", "sum"),
        video=("video", lambda s: " + ".join(sorted(set(s))))).reset_index()
    g["busy_s"] = g[["busy_s", "covered_s"]].min(axis=1)
    g["idle_s"] = g["covered_s"] - g["busy_s"]
    g["busy_pct"] = (100 * g["busy_s"] / g["covered_s"]).round(1)
    g["idle_pct"] = (100 * g["idle_s"] / g["covered_s"]).round(1)
    g["avg_handle_s"] = g["hv"].apply(lambda x: round(np.mean(x), 1) if x else np.nan)
    g["median_handle_s"] = g["hv"].apply(lambda x: round(np.median(x), 1) if x else np.nan)
    g["max_handle_s"] = g["hv"].apply(lambda x: round(max(x), 1) if x else np.nan)
    g["clears_per_busy_min"] = np.where(g["busy_s"] > 0, (g["cleared"] / (g["busy_s"] / 60)).round(2), np.nan)
    g["avg_next_alert_gap_s"] = g["gaps"].apply(lambda x: round(np.mean(x), 1) if x else np.nan)
    g["partial_bucket"] = g["covered_s"] < pd.Timedelta(freq).total_seconds() - 1
    for c in ["covered_s", "busy_s", "idle_s"]:
        g[c] = g[c].round(1)
    cols = ["bucket_start", "video", "covered_s", "cleared", "arrived", "investigated", "busy_s", "idle_s", "busy_pct",
            "idle_pct", "avg_handle_s", "median_handle_s", "max_handle_s", "clears_per_busy_min",
            "avg_next_alert_gap_s", "partial_bucket"]
    return g[cols]


for f, name in [("1min", "1min"), ("5min", "5min"), ("15min", "15min")]:
    buckets(f).to_csv(os.path.join(AOUT, f"buckets_{name}.csv"), index=False)

# by alert type
hv = al[al["handle_valid"]]
bt = hv.groupby("alert_name")["duration_s"].agg(
    count="count", avg_s="mean", median_s="median", p90_s=lambda s: s.quantile(.9), max_s="max").round(1)
bt.sort_values("count", ascending=False).to_csv(os.path.join(AOUT, "by_alert_type.csv"))


def summarize(a, c, label):
    covered = (c["max"] - c["min"]).dt.total_seconds().sum()
    idle = wt[wt["video"].isin(c["video"])]["duration_s"].sum()
    busy = covered - idle
    per_h = lambda n, s: round(n / (s / 3600), 1) if s > 0 else np.nan  # noqa: E731
    h = a[a["handle_valid"]]["duration_s"]
    return dict(session=label, start=c["min"].min(), end=c["max"].max(),
                recorded_min=round(covered / 60, 1), alerts_seen=len(a), alerts_cleared=int(a["removed"].sum()),
                busy_pct=round(100 * busy / covered, 1) if covered else np.nan,
                idle_pct=round(100 - 100 * busy / covered, 1) if covered else np.nan,
                avg_handle_s=round(h.mean(), 1), median_handle_s=round(h.median(), 1),
                p90_handle_s=round(h.quantile(.9), 1),
                clears_per_recorded_hour=per_h(a["removed"].sum(), covered),
                clears_per_busy_hour=per_h(a["removed"].sum(), busy),
                alerts_investigated=int(a["investigated"].sum()),
                median_next_alert_gap_s=round(a["next_alert_gap_s"].median(), 1),
                pct_back_to_back=round(100 * (a["next_alert_gap_s"] < 1).mean(), 1),
                flagged_alerts=int((a["flags"].str.replace("visible_at_start", "").str.strip(";") != "").sum()))


rows = [summarize(al[al["video"] == v], cov[cov["video"] == v], v) for v in cov["video"]]
rows.append(summarize(al, cov, "ALL"))
ss = pd.DataFrame(rows)
ss.to_csv(os.path.join(AOUT, "session_summary.csv"), index=False)
pd.set_option("display.width", 250)
print(ss.drop(columns=["start", "end"]).to_string(index=False))
