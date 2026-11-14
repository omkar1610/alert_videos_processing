#!/bin/bash
# Double-click to process new recordings and open the report.
# Works next to run_all.py, or anywhere (e.g. the Desktop) once setup.sh has filled in PROJECT.
# Everything is inside main(), which bash reads in full before running it, so the
# script keeps working even if this file is replaced while it runs (setup.sh does that).
main() {
PROJECT="__PROJECT__"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/run_all.py" ] && PROJECT="$HERE"

if [ ! -f "$PROJECT/run_all.py" ]; then
  echo "Can't find the project folder (run_all.py). Run 'bash setup.sh' in it once."
  read -r -p "Press Enter to close." _; exit 1
fi
cd "$PROJECT" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "First time: setting up Python..."
  bash setup.sh || { read -r -p "Setup failed. Press Enter to close." _; exit 1; }
fi

.venv/bin/python run_all.py --fast "$@"
status=$?
if [ $status -eq 0 ]; then
  [ -f output/report.pdf ] && open output/report.pdf || open output/report.html
  echo; echo "Finished. The report is open."
else
  echo; echo "Something failed (see the messages above)."
fi
read -r -p "Press Enter to close this window." _
}
main "$@"; exit  # keep on one line: nothing after this is read
