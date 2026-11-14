"""Shape analysis output into the small tables the report page reads (output/report/)."""
import os
import re

import pandas as pd

OUT = os.environ.get("OUT", "output")
A = os.path.join(OUT, "analysis")
R = os.path.join(OUT, "report")
os.makedirs(R, exist_ok=True)


def label(video):
    m = re.search(r"(\d{4}-\d{2}-\d{2}) at (\d{1,2})\.(\d{2})\.\d{2}\W*([AP]M)", str(video))
    if not m:
        return "All sessions"
    d = pd.Timestamp(m.group(1))
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}, {int(m.group(2))}:{m.group(3)} {m.group(4)}"


m = pd.read_csv(os.path.join(A, "buckets_1min.csv"), parse_dates=["bucket_start"])
m["session"] = m["video"].map(label)
m["minute"] = m["bucket_start"].dt.strftime("%Y-%m-%dT%H:%M")
m["clock"] = m["bucket_start"].dt.strftime("%-I:%M %p")
m = m[["session", "minute", "clock", "covered_s", "cleared", "avg_handle_s", "idle_pct", "partial_bucket"]]
m = m.rename(columns={"covered_s": "recorded_s", "partial_bucket": "partial_minute"})
m.to_csv(os.path.join(R, "minutes.csv"), index=False)

s = pd.read_csv(os.path.join(A, "session_summary.csv"), parse_dates=["start", "end"])
s["label"] = s["session"].map(label)
s["order"] = 99
real = s["session"] != "ALL"
s.loc[real, "order"] = s.loc[real, "start"].rank(method="first").astype(int)
s["clears_per_hour"] = s["clears_per_recorded_hour"]
s["start_time"] = s["start"].dt.strftime("%H:%M:%S")
s["end_time"] = s["end"].dt.strftime("%H:%M:%S")
s.loc[s["session"] == "ALL", ["start_time", "end_time"]] = ""
s["clears_per_min"] = (s["alerts_cleared"] / s["recorded_min"]).round(2)
s["clears_per_busy_min"] = (s["clears_per_busy_hour"] / 60).round(2)  # minutes not spent waiting
s = s.sort_values("order")[["label", "order", "start_time", "end_time", "recorded_min", "alerts_cleared", "clears_per_min", "clears_per_busy_min", "clears_per_hour",
                            "median_handle_s", "avg_handle_s", "p90_handle_s", "idle_pct", "alerts_investigated"]]
s = s.rename(columns={"label": "session"})
s.to_csv(os.path.join(R, "sessions.csv"), index=False)

t = pd.read_csv(os.path.join(A, "by_alert_type.csv"))
t["alert_name"] = t["alert_name"].str.replace(r"^[A-Z]\s+", "", regex=True)  # severity badge read as text
alerts = pd.read_csv(os.path.join(OUT, "all_alerts.csv"))
alerts = alerts[(alerts["status"] == "removed") & ~alerts["flags"].fillna("").str.contains("visible_at_start")].copy()
alerts["alert_name"] = alerts["alert_name"].fillna("").str.replace(r"^[A-Z]\s+", "", regex=True)
counts = alerts["alert_name"].value_counts()
alerts["alert_type"] = alerts["alert_name"].where(alerts["alert_name"].map(counts) >= 10, "All other types")
alerts.loc[alerts["alert_type"].str.strip() == "", "alert_type"] = "All other types"
g = alerts.groupby("alert_type")["duration_s"]
t = pd.DataFrame({"alerts": g.size(), "median_s": g.median().round(1), "p90_s": g.quantile(.9).round(1),
                  "avg_s": g.mean().round(1)}).reset_index()
t["share"] = (t["alerts"] / t["alerts"].sum()).round(3)
t["_o"] = t["alert_type"].eq("All other types")
t = t.sort_values(["_o", "alerts"], ascending=[True, False]).drop(columns="_o")
t.to_csv(os.path.join(R, "types.csv"), index=False)
print(s.to_string(index=False))
print(t.to_string(index=False))
print(len(m), "minute rows")
