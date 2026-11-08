#!/bin/bash
# One-time setup on a Mac: Python 3.14 -> .venv -> packages -> check that run_all.py works.
# Run from anywhere:   bash setup.sh
# Safe to run again (reuses .venv and upgrades the packages).
set -e
cd "$(dirname "$0")"

say()  { printf "\n== %s\n" "$1"; }
fail() { printf "\nSETUP FAILED: %s\n" "$1"; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "macOS only (text recognition uses Apple's Vision framework)."

# 1. Python 3.14 -------------------------------------------------------------
say "1/4 Python 3.14"
PY=""
for c in python3.14 /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 \
         /Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14; do
  if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Not found. Installing with Homebrew: brew install python@3.14"
    brew install python@3.14
    PY="$(brew --prefix python@3.14)/bin/python3.14"
  else
    fail "Python 3.14 not found and Homebrew isn't installed.
  Install Python 3.14 from https://www.python.org/downloads/macos/ (the macOS installer),
  then run: bash setup.sh"
  fi
fi
echo "Using $PY ($("$PY" --version))"

# 2. Virtual environment ------------------------------------------------------
say "2/4 Virtual environment (.venv)"
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 14))' 2>/dev/null; then
  echo ".venv uses a different Python, recreating it"
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  echo "created .venv"
else
  echo "reusing existing .venv"
fi

# 3. Packages (latest versions, from requirements.txt) ------------------------
say "3/4 Packages"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --upgrade -r requirements.txt
.venv/bin/python -m pip list 2>/dev/null | grep -iE '^(opencv-python-headless|ocrmac|numpy|pandas|pillow)[[:space:]]'

# 4. Check run_all.py will run ------------------------------------------------
say "4/4 Checking run_all.py"
.venv/bin/python - <<'EOF'
import sys
for mod in ["cv2", "ocrmac", "numpy", "pandas", "PIL"]:
    __import__(mod)
from ocrmac import ocrmac            # Apple Vision text recognition, tried on a tiny test image
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (900, 120), "white")
ImageDraw.Draw(img).text((20, 30), "Alert 123456789", fill="black", font=ImageFont.load_default(size=56))
txt = " ".join(t for t, *_ in ocrmac.OCR(img, recognition_level="accurate").recognize())
print("all packages import OK; text recognition test read:", repr(txt))
if "123456789" not in txt.replace(" ", ""):
    sys.exit("text recognition did not read the test ID correctly")
EOF
.venv/bin/python run_all.py --help >/dev/null || fail "run_all.py --help did not run"
echo "run_all.py OK"

BROWSER=""
for b in "Google Chrome" "Chromium" "Microsoft Edge" "Brave Browser"; do
  [ -d "/Applications/$b.app" ] && BROWSER="$b" && break
done
if [ -n "$BROWSER" ]; then
  echo "$BROWSER found: the PDF report will be made"
else
  echo "NOTE: no Chrome/Chromium/Edge/Brave in /Applications, so the PDF is skipped (HTML report still made)"
fi

printf "\nSetup done. To process recordings:\n  .venv/bin/python run_all.py\n"
