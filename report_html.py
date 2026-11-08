"""Build output/report.html: one self-contained file (no internet needed) that can be
emailed or attached. Reads output/report/{sessions,minutes,types}.csv."""
import html
import os

import pandas as pd

OUT = os.environ.get("OUT", "output")
R = os.path.join(OUT, "report")
s = pd.read_csv(os.path.join(R, "sessions.csv")).sort_values("order")
m = pd.read_csv(os.path.join(R, "minutes.csv"))
t = pd.read_csv(os.path.join(R, "types.csv"))
e = html.escape

tot = s[s["session"] == "All sessions"].iloc[0]
per = s[s["session"] != "All sessions"]
avg = float(tot["clears_per_min"])


def f1(x):
    return "—" if pd.isna(x) else f"{float(x):.1f}"


def fi(x):
    return "—" if pd.isna(x) else f"{int(x):,}"


# ---------- per-minute small multiples (inline SVG, one per session, shared scale) ----------
W, H, ml, mr, mt, mb = 900, 96, 30, 8, 6, 20
max_len = max(len(m[m["session"] == r["session"]]) for _, r in per.iterrows())
y_max = max(m["cleared"].max(), avg)
y_max = int(-(-y_max // 4) * 4)  # round up to a multiple of 4
band = (W - ml - mr) / max_len
bw = band * 0.8


def y(v):
    return mt + (H - mt - mb) * (1 - v / y_max)


charts = []
for _, ses in per.iterrows():
    d = m[m["session"] == ses["session"]].reset_index(drop=True)
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{e(ses["session"])}: alerts cleared per minute">']
    for tk in range(0, y_max + 1, max(1, y_max // 4)):
        parts.append(f'<line class="grid" x1="{ml}" x2="{W - mr}" y1="{y(tk):.1f}" y2="{y(tk):.1f}"/>'
                     f'<text class="tick" x="{ml - 6}" y="{y(tk):.1f}" dy="0.32em" text-anchor="end">{tk}</text>')
    for i, r in d.iterrows():
        x = ml + i * band + (band - bw) / 2
        cls = "wait" if r["idle_pct"] >= 1 else "work"
        op = ' opacity="0.45"' if str(r["partial_minute"]).lower() == "true" else ""
        h = max(0.0, y(0) - y(r["cleared"]))
        tip = (f'{r["clock"]}: {int(r["cleared"])} cleared'
               + (f', avg {r["avg_handle_s"]:.1f} s per alert' if pd.notna(r["avg_handle_s"]) else "")
               + f', waiting {r["idle_pct"]:.0f}% of the minute'
               + (" (part minute)" if op else ""))
        parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y(r["cleared"]):.1f}" width="{bw:.1f}" height="{h:.1f}" '
                     f'rx="2"{op}><title>{e(tip)}</title></rect>')
        if i % 5 == 0:
            parts.append(f'<text class="tick" x="{x + bw / 2:.1f}" y="{H - 5}" text-anchor="middle">'
                         f'{e(r["clock"].replace(" AM", "").replace(" PM", ""))}</text>')
    parts.append(f'<line class="ref" x1="{ml}" x2="{W - mr}" y1="{y(avg):.1f}" y2="{y(avg):.1f}"/></svg>')
    charts.append(
        f'<div class="row"><div class="rl">{e(ses["session"])}<span>{fi(ses["alerts_cleared"])} cleared · '
        f'{f1(ses["clears_per_min"])} per min · median {f1(ses["median_handle_s"])} s</span></div>{"".join(parts)}</div>')

# ---------- tables ----------
srows = "".join(
    f'<tr{" class=total" if r["session"] == "All sessions" else ""}><td>{e(r["session"])}</td>'
    f'<td>{e(str(r["start_time"]) if pd.notna(r["start_time"]) else "")}</td><td>{e(str(r["end_time"]) if pd.notna(r["end_time"]) else "")}</td>'
    f'<td>{f1(r["recorded_min"])}</td><td>{fi(r["alerts_cleared"])}</td><td>{f1(r["clears_per_min"])}</td>'
    f'<td>{f1(r["median_handle_s"])}</td><td>{f1(r["p90_handle_s"])}</td><td>{f1(r["idle_pct"])}</td></tr>'
    for _, r in s.iterrows())
tmax = t["median_s"].max()
trows = "".join(
    f'<tr><td>{e(r["alert_type"])}</td><td>{fi(r["alerts"])}</td>'
    f'<td><span class="bcell">{f1(r["median_s"])}<i><b class="bar" style="width:{100 * r["median_s"] / tmax:.0f}%"></b></i></span></td>'
    f'<td>{f1(r["p90_s"])}</td></tr>'
    for _, r in t.iterrows())

slow = per.sort_values("clears_per_min").iloc[0]
fast = per.sort_values("clears_per_min").iloc[-1]
days = list(dict.fromkeys(x.split(",")[0] for x in per["session"]))
dates = days[0] if len(days) == 1 else ", ".join(days[:-1]) + " and " + days[-1]

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alert handling pace</title>
<style>
  /* Layout: one centred column; headline row, then chart card, then two tables side by side. */
  :root {{
    --bg: #f6f7f9; --panel: #ffffff; --fg: #1d232b; --muted: #5d6673; --line: #dfe3e8;
    --work: #3165c4; --wait: #b4bcc7; --ref: #c2410c; --grid: #eceff3;
    --serif: "Iowan Old Style", "Charter", Georgia, serif;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #14181d; --panel: #1b2027; --fg: #e7ebf0; --muted: #9aa4b1; --line: #2c333d;
             --work: #6b9bf0; --wait: #4b5563; --ref: #fb923c; --grid: #232a33; color-scheme: dark; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 var(--sans); }}
  .wrap {{ max-width: 72rem; margin: 0 auto; padding: 32px max(16px, 3vw) 48px; display: flex; flex-direction: column; gap: 28px; }}
  h1 {{ font: 400 2rem/1.15 var(--serif); margin: 0; text-wrap: balance; }}
  h2 {{ font-size: 1.05rem; font-weight: 600; margin: 0; }}
  .lede {{ max-width: 64ch; margin: 10px 0 0; }}
  .lede b {{ font-variant-numeric: tabular-nums; }}
  .note {{ font-size: 0.82rem; color: var(--muted); margin: 4px 0 0; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr)); gap: 12px; }}
  .kpi, .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
  .kpi {{ padding: 14px 18px; display: flex; flex-direction: column; gap: 4px; }}
  .kpi .k {{ font-size: 0.8rem; color: var(--muted); }}
  .kpi .v {{ font: 400 1.9rem/1.1 var(--serif); font-variant-numeric: tabular-nums; }}
  .kpi .u {{ font: 0.95rem var(--sans); color: var(--muted); margin-left: 2px; }}
  .card {{ padding: 20px; min-width: 0; }}
  .grid2 {{ display: flex; flex-direction: column; gap: 12px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.8rem; color: var(--muted); margin: 10px 0 14px; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .sw {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .sw.work {{ background: var(--work); }} .sw.wait {{ background: var(--wait); }}
  .sw.line {{ width: 14px; height: 0; border-top: 2px dashed var(--ref); border-radius: 0; }}
  .row {{ margin-bottom: 14px; }}
  .rl {{ font-size: 0.85rem; font-weight: 600; }}
  .rl span {{ font-weight: 400; color: var(--muted); margin-left: 8px; font-variant-numeric: tabular-nums; }}
  svg {{ display: block; max-width: 100%; }}
  svg .grid {{ stroke: var(--grid); stroke-width: 1; }}
  svg .tick {{ fill: var(--muted); font: 11px var(--sans); }}
  svg .work {{ fill: var(--work); }} svg .wait {{ fill: var(--wait); }}
  svg rect:hover {{ stroke: var(--fg); stroke-width: 1; }}
  svg .ref {{ stroke: var(--ref); stroke-width: 1.5; stroke-dasharray: 4 3; }}
  .tbl {{ overflow-x: auto; margin-top: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; font-variant-numeric: tabular-nums; }}
  th {{ text-align: right; font-weight: 600; color: var(--muted); padding: 6px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
  td {{ text-align: right; padding: 7px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
  th:first-child, td:first-child {{ text-align: left; white-space: normal; }}
  tr.total td {{ font-weight: 700; border-bottom: none; }}
  .bcell {{ display: inline-flex; align-items: center; justify-content: flex-end; gap: 8px; }}
  .bcell i {{ display: inline-block; width: 6rem; }}
  .bar {{ display: block; height: 8px; border-radius: 2px; background: var(--work); opacity: .8; }}
  .method {{ font-size: 0.84rem; color: var(--muted); }}
  dl {{ display: grid; grid-template-columns: minmax(8rem, 13rem) minmax(0, 1fr); gap: 6px 16px; margin: 12px 0 0; }}
  dt {{ color: var(--fg); }} dd {{ margin: 0; }}
  @page {{ size: A4; margin: 12mm; }}
  @media print {{
    :root {{ --bg: #ffffff; --panel: #ffffff; --fg: #1d232b; --muted: #4b5563; --line: #d5dae0;
             --work: #3165c4; --wait: #b4bcc7; --ref: #c2410c; --grid: #eceff3; color-scheme: light; }}
    * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    body {{ font-size: 12px; }}
    .wrap {{ max-width: none; padding: 0; gap: 14px; }}
    h1 {{ font-size: 1.6rem; }}
    .card, .kpi {{ break-inside: avoid; }}
    .card {{ padding: 12px 14px; }}
    .kpi {{ padding: 8px 12px; }} .kpi .v {{ font-size: 1.45rem; }}
    .kpis {{ grid-template-columns: repeat(5, 1fr); }}
    .row {{ break-inside: avoid; margin-bottom: 6px; }}
    .hover-hint {{ display: none; }}
  }}
  @media (max-width: 560px) {{ dl {{ grid-template-columns: 1fr; }} .bcell i {{ width: 3.5rem; }} h1 {{ font-size: 1.6rem; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Alert handling pace</h1>
    <p class="lede">Across {len(per)} screen recordings from {dates}, I cleared <b>{fi(tot["alerts_cleared"])}</b> alerts in
    <b>{f1(tot["recorded_min"])}</b> recorded minutes, a median of <b>{f1(tot["median_handle_s"])}</b> seconds per alert.
    The queue was empty for only <b>{f1(tot["idle_pct"])}%</b> of that time, so the per-minute counts show handling speed
    rather than how many alerts arrived. The fastest session was {e(fast["session"])} at <b>{f1(fast["clears_per_min"])}</b> per minute;
    the slowest was {e(slow["session"])} at <b>{f1(slow["clears_per_min"])}</b>.</p>
  </header>

  <section class="kpis" aria-label="Headline figures">
    <div class="kpi"><span class="k">Alerts cleared</span><span class="v">{fi(tot["alerts_cleared"])}</span></div>
    <div class="kpi"><span class="k">Cleared per minute</span><span class="v">{f1(tot["clears_per_min"])}</span></div>
    <div class="kpi"><span class="k">Median time per alert</span><span class="v">{f1(tot["median_handle_s"])}<span class="u">s</span></span></div>
    <div class="kpi"><span class="k">9 in 10 alerts done within</span><span class="v">{f1(tot["p90_handle_s"])}<span class="u">s</span></span></div>
    <div class="kpi"><span class="k">Queue empty (waiting)</span><span class="v">{f1(tot["idle_pct"])}<span class="u">%</span></span></div>
  </section>

  <section class="card">
    <h2>By session</h2>
    <p class="note">Start and end are the first and last recorded second, in Mac local time (24-hour), for matching against Grafana. Time per alert runs from when it appeared to when it was cleared.</p>
    <div class="tbl"><table>
      <thead><tr><th>Session</th><th>Start</th><th>End</th><th>Minutes</th><th>Cleared</th><th>Per min</th><th>Median s</th><th>90th pct s</th><th>Waiting %</th></tr></thead>
      <tbody>{srows}</tbody></table></div>
  </section>

  <section class="card">
    <h2>Alerts cleared per minute</h2>
    <p class="note">Each bar is one clock minute, one row per recording, all on the same scale. <span class="hover-hint">Hover a bar for details.</span>
    Faded bars at the ends are part-minutes.</p>
    <div class="legend"><span><i class="sw work"></i>Alerts in queue the whole minute</span>
      <span><i class="sw wait"></i>Some time waiting for alerts</span>
      <span><i class="sw line"></i>Average, {f1(avg)} per minute</span></div>
    {"".join(charts)}
  </section>

  <div class="grid2">
    <section class="card">
      <h2>Time per alert by alert type</h2>
      <p class="note">Types with 10 or more alerts; the rest are grouped. Bars show the median on one scale.</p>
      <div class="tbl"><table>
        <thead><tr><th>Alert type</th><th>Alerts</th><th>Median s</th><th>90th pct s</th></tr></thead>
        <tbody>{trows}</tbody></table></div>
    </section>
  </div>

  <section class="card method">
    <h2>How these numbers were measured</h2>
    <dl>
      <dt>Source</dt><dd>macOS screen recordings of the Alert Verification Portal. Every frame was decoded locally and the alert ID was read from the panel with on-device text recognition. Nothing left the machine.</dd>
      <dt>Clock time</dt><dd>Each recording's start was pinned to the exact frame where the menu-bar clock changed minute, then carried forward with the video's own timestamps. Accurate to about a frame (1/60 s).</dd>
      <dt>Time per alert</dt><dd>From the first frame an alert ID appeared to the first frame it was gone. Time in the live camera view or the verify dialog for the same alert counts toward that alert.</dd>
      <dt>Waiting</dt><dd>Time the portal showed "Waiting for alerts to arrive". Gaps between recordings are not counted.</dd>
      <dt>Cleared per minute</dt><dd>Alerts whose removal fell inside that clock minute, regardless of when they appeared.</dd>
      <dt>Left out</dt><dd>Alerts already on screen when a recording started are left out of time-per-alert figures, since their real start is unknown. Alerts still on screen when a recording ended are not counted as cleared.</dd>
    </dl>
  </section>
</div>
</body>
</html>
"""
path = os.path.join(OUT, "report.html")
open(path, "w").write(page)
print("wrote", path)
