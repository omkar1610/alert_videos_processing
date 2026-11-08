# Alert handling pace from screen recordings

Reads macOS screen recordings of the Alert Verification Portal, works out when each
alert appeared and was cleared, and builds a report (PDF + HTML) of how fast alerts
were handled. Everything runs locally on the Mac: no video frames or data leave it.

## Setting up on a new Mac

Needs **macOS** (the text recognition uses Apple's built-in Vision framework, so it
won't run on Windows or Linux). Apple Silicon (M1–M4) is fastest; Intel Macs work but are slower.

**1. Copy the project folder.** These files are needed:

```
run_all.py  extract.py  combine.py  analyze.py  report_data.py  report_html.py  report_pdf.py
requirements.txt  README.md
```

Don't copy `.venv/` (it belongs to the old machine and won't work on a new one).
`output/` is optional: copy it to keep past results, and those videos won't be reprocessed.

**2. Check Python 3 is installed.** In Terminal:

```bash
python3 --version
```

If it prints `Python 3.10` or newer, go to step 3. If not, install it with one of:
- the macOS installer from https://www.python.org/downloads/, or
- Homebrew: `brew install python`

(If macOS offers to install "command line developer tools" the first time you type
`python3`, accept it; that also works.)

**3. Create the virtual environment and install the packages.** From inside the project folder:

```bash
cd ~/path/to/vid_extract
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

This creates a private `.venv` folder for this project only; nothing is installed system-wide.
If a pinned version fails to install on a different Python version, install without versions:
`.venv/bin/pip install opencv-python-headless ocrmac numpy pandas pillow`.

**4. Check it works:**

```bash
.venv/bin/python run_all.py --help
```

If it prints the usage text, you're set. If it says a package is missing, it prints the install command to run.

**5. For the PDF**, install Google Chrome (or Chromium / Edge / Brave). Without it,
everything else still runs; open `output/report.html` and use File > Print > Save as PDF.

Always run the scripts with `.venv/bin/python` (not plain `python3`), so they use the
packages from step 3. Alternatively run `source .venv/bin/activate` once per Terminal
window, then `python run_all.py` works.

## Adding new recordings

1. **Record as usual.** macOS saves recordings to the Desktop as
   `Screen Recording 2026-11-08 at 7.12.03 AM.mov`. Leave them there with the original
   name: the date and time in the name are how a recording is placed on the clock.
   (You can also drop a `.mov` into this folder yourself; that works too.)

2. **Run:**

   ```bash
   .venv/bin/python run_all.py
   ```

3. **Open** `output/report.pdf` (or `output/report.html`).

### Recordings are moved in, then deleted

- **Start:** every `Screen Recording *.mov` on the Desktop is moved into this folder.
  A file with the same name here is replaced.
- **End** (only if every step succeeded): each recording in this folder whose
  `output/<video name>/alerts.csv` exists is deleted.

Deletes are permanent (not to the Bin). The report only reads `output/`, so it keeps
including deleted recordings, but they can't be re-read later with `--force`.
`--keep-videos` skips both the move and the delete.

The first time, macOS may ask whether Terminal can access the Desktop folder: allow it.

### What the run does

| Step | Script | What it does | Only new videos? |
|---|---|---|---|
| 0 | `run_all.py` | Moves new recordings from the Desktop into this folder | n/a |
| 1 | `extract.py` | Reads the alert ID frame by frame, pins the start to the on-screen clock, writes `output/<video name>/` | **Yes.** Videos that already have a folder are skipped |
| 2 | `combine.py` | Cleans and merges **all** videos into `output/all_*.csv` | No, rebuilds from everything |
| 3 | `analyze.py` | 1 / 5 / 15 minute buckets, session summary, alert types → `output/analysis/` | No, rebuilds |
| 4 | `report_data.py` | Report tables → `output/report/` | No, rebuilds |
| 5 | `report_html.py` | `output/report.html`, a single file that works offline | No, rebuilds |
| 6 | `report_pdf.py` | `output/report.pdf` (A4), printed with local Chrome | No, rebuilds |
| end | `run_all.py` | Deletes the recordings that now have results | n/a |

The report always covers every recording processed so far. A new recording gets its own
row in "By session" and its own per-minute chart, and the totals and summary text
update automatically.

**Time:** about half the video's length per video, up to 4 videos in parallel (a
25-minute recording takes about 11 minutes). Steps 2–6 take seconds. An interrupted run
resumes: finished videos are kept and skipped next time, and no video is deleted unless
the whole run finished.

## Options

```bash
.venv/bin/python run_all.py --force                       # redo every video
.venv/bin/python run_all.py --force --only "2026-11-08"   # redo only videos whose name contains this
.venv/bin/python run_all.py --workers 2                   # fewer videos in parallel
.venv/bin/python run_all.py --keep-videos                 # don't move from Desktop, don't delete videos
```

## Things to know

- **Removing a video from the report:** delete its folder in `output/`
  (e.g. `output/Screen Recording 2026-11-06 at 8.44.08 AM/`) and run again. Deleting only
  the `.mov` keeps it in the report (that's what the automatic clean-up relies on).
- **`sample_video.mov` is always ignored**, since it has no time in its name.
- **Times** are Mac local time. Session start and end in the report are the first and
  last recorded second (24-hour), for matching against Grafana. Shift them if Grafana is
  set to another timezone.
- **If the portal layout changes a lot** (the alert panel moves out of the middle-lower
  part of the screen, or the "Waiting for alerts to arrive" text changes), the reading
  area may need adjusting in `extract.py`. After a run, check the `flags` column in
  `output/all_alerts.csv`: many `short_lived` rows or IDs that don't look like the usual
  9-digit ones mean something shifted.

## Output files

| File | Contents |
|---|---|
| `output/report.pdf`, `output/report.html` | The report to share |
| `output/all_alerts.csv` | One row per alert: ID, type, start and end clock time, duration, flags |
| `output/all_observations.csv` | Raw dump: every reading of the screen, with what was on it |
| `output/all_waiting.csv` | Periods the screen said "Waiting for alerts to arrive" |
| `output/all_alerts_raw.csv` | Alert rows before cleaning, for checking |
| `output/analysis/buckets_1min.csv`, `buckets_5min.csv`, `buckets_15min.csv` | Per-bucket cleared, arrived, waiting %, time per alert |
| `output/analysis/session_summary.csv` | One row per recording plus all combined |
| `output/analysis/by_alert_type.csv` | Time per alert by alert type |

## How the numbers are measured

- **Alert ID:** read with Apple's on-device text recognition from the panel line
  "Alert name • 123456789". Transitions are found to the exact video frame.
- **Clock time:** each recording's start is pinned to the frame where the menu-bar clock
  changes minute, then carried forward with the video's own timestamps (accurate to
  about 1/60 s).
- **Time per alert:** first frame the ID appeared to the first frame it was gone. Time in
  the live camera view or the verify dialog for the same alert counts toward that alert.
- **Waiting:** time the portal showed "Waiting for alerts to arrive". Gaps between
  recordings are not counted.
- **Cleared per minute:** alerts whose removal fell inside that clock minute.
- **Left out:** alerts already on screen when a recording started (unknown start) are left
  out of time-per-alert figures; alerts still on screen when a recording ended are not
  counted as cleared.
