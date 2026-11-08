#!/usr/bin/env python3
"""End-to-end alert extraction + analysis for Mac screen recordings.

Put this file next to extract.py, combine.py, analyze.py, report_data.py and the
.mov recordings, then run:

    python3 run_all.py                 # process every *.mov in this folder
    python3 run_all.py --force         # redo videos that were already processed
    python3 run_all.py --only "2026-11-07"   # only videos whose name contains this

Steps
  1. extract.py     each video -> output/<video>/observations.csv, alerts.csv, time_anchor.txt
                    (videos already done are skipped, so an interrupted run resumes)
  2. combine.py     -> output/all_observations.csv, all_alerts.csv, all_alerts_raw.csv, all_waiting.csv
  3. analyze.py     -> output/analysis/buckets_1min/5min/15min.csv, by_alert_type.csv, session_summary.csv
  4. report_data.py -> output/report/minutes.csv, sessions.csv, types.csv
  5. report_html.py -> output/report.html (single self-contained file to email or attach)
  6. report_pdf.py  -> output/report.pdf (printed with local Chrome; skipped if no Chrome)

Before step 1, every "Screen Recording *.mov" on the Desktop is moved into this folder
(replacing a file with the same name). After step 6, every recording here whose
output/<video>/alerts.csv exists is deleted. --keep-videos skips both.

First run on a new Mac (see README.md):
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python run_all.py
Everything runs locally (Apple Vision OCR); no frames leave the machine.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
DESKTOP = os.path.expanduser("~/Desktop")
NEEDED = ["cv2", "ocrmac", "numpy", "pandas", "PIL"]


def check_deps():
    missing = []
    for mod in NEEDED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit(f"Missing Python packages: {', '.join(missing)}\n"
                 f"Install with:\n  {sys.executable} -m pip install -r requirements.txt")
    for f in ["extract.py", "combine.py", "analyze.py", "report_data.py", "report_html.py", "report_pdf.py"]:
        if not os.path.exists(os.path.join(HERE, f)):
            sys.exit(f"{f} not found next to run_all.py")


def import_from_desktop():
    """Move every screen recording from the Desktop into this folder (same name is replaced)."""
    print(f"\n=== 0/6 Move recordings from {DESKTOP} ===", flush=True)
    for src in sorted(glob.glob(os.path.join(DESKTOP, "Screen Recording *.mov"))):
        shutil.move(src, os.path.join(HERE, os.path.basename(src)))
        print("   moved:", os.path.basename(src))


def delete_processed():
    """Delete each recording here whose output folder has alerts.csv."""
    print("\n=== Clean up: delete processed recordings ===", flush=True)
    for v in glob.glob(os.path.join(HERE, "*.mov")) + glob.glob(os.path.join(HERE, "*.mp4")):
        stem = os.path.splitext(os.path.basename(v))[0]
        if os.path.exists(os.path.join(OUT, stem, "alerts.csv")):
            os.remove(v)
            print("   deleted:", os.path.basename(v))


def step(title, args):
    print(f"\n=== {title} ===", flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, *args], cwd=HERE, env={**os.environ, "OUT": OUT})
    if r.returncode != 0:
        sys.exit(f"{title} failed (exit {r.returncode})")
    print(f"--- {title}: {time.time() - t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="reprocess videos that already have results")
    ap.add_argument("--only", default=None, help="only videos whose file name contains this text")
    ap.add_argument("--fps", type=float, default=2.0, help="OCR samples per second (transitions are refined to the exact frame)")
    ap.add_argument("--workers", type=int, default=4, help="videos processed in parallel")
    ap.add_argument("--keep-videos", action="store_true",
                    help="don't import from the Desktop and don't delete recordings after processing")
    a = ap.parse_args()

    check_deps()
    if not a.keep_videos:
        import_from_desktop()
    videos = sorted(glob.glob(os.path.join(HERE, "*.mov")) + glob.glob(os.path.join(HERE, "*.mp4")))
    videos = [v for v in videos if os.path.basename(v) != "sample_video.mov"]
    if a.only:
        videos = [v for v in videos if a.only in os.path.basename(v).replace(" ", " ")]
    if not videos and not glob.glob(os.path.join(OUT, "*", "alerts.csv")):
        sys.exit("No .mov/.mp4 recordings found next to run_all.py or on the Desktop, and no past results")
    print(f"{len(videos)} recording(s) in this folder:")
    for v in videos:
        print("  ", os.path.basename(v))

    os.makedirs(OUT, exist_ok=True)
    if a.force:
        for v in videos:
            d = os.path.join(OUT, os.path.splitext(os.path.basename(v))[0])
            if os.path.isdir(d):
                shutil.rmtree(d)

    if videos:
        step("1/6 Extract alert IDs from video",
             ["extract.py", *videos, "--fps", str(a.fps), "--workers", str(a.workers), "--out", OUT])
    else:
        print("\n=== 1/6 Extract: no recordings to process, rebuilding the report from past results ===")
    step("2/6 Combine and clean", ["combine.py"])
    step("3/6 Analyse (1 / 5 / 15 minute buckets)", ["analyze.py"])
    step("4/6 Report tables", ["report_data.py"])
    step("5/6 Standalone HTML report", ["report_html.py"])
    step("6/6 PDF report", ["report_pdf.py"])

    # Only reached when every step above succeeded (step() exits on failure).
    if not a.keep_videos:
        delete_processed()

    print("\nDone. Main files:\n   output/report.pdf\n   output/report.html")
    for f in ["all_alerts.csv", "all_observations.csv", "analysis/buckets_1min.csv", "analysis/buckets_5min.csv",
              "analysis/buckets_15min.csv", "analysis/session_summary.csv", "analysis/by_alert_type.csv"]:
        print("  ", os.path.join("output", f))


if __name__ == "__main__":
    main()
