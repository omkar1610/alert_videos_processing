"""Print output/report.html to output/report.pdf with local headless Chrome (nothing leaves the Mac)."""
import os
import shutil
import subprocess
import sys

OUT = os.environ.get("OUT", "output")
src = os.path.abspath(os.path.join(OUT, "report.html"))
dst = os.path.abspath(os.path.join(OUT, "report.pdf"))

candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
]
browser = next((c for c in candidates if c and os.path.exists(c)), None)
if not browser:
    print("No Chrome/Chromium/Edge found: skipped PDF. Open output/report.html and use File > Print > Save as PDF.")
    sys.exit(0)

if os.path.exists(dst):
    os.remove(dst)
cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--hide-scrollbars",
       f"--print-to-pdf={dst}", "file://" + src]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
if not os.path.exists(dst):
    print(r.stdout[-500:], r.stderr[-500:])
    sys.exit("PDF was not created")
print("wrote", dst)
