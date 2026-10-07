import os
import shutil
import subprocess
import tempfile
import sys

ROOT = os.path.abspath("tmp")

def mk():
    d = tempfile.mkdtemp(prefix="dbg_x_", dir=ROOT)
    with open(os.path.join(d, "x.txt"), "w") as f:
        f.write("hi")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "sub", "y.txt"), "w") as f:
        f.write("yo")
    return d

mode = sys.argv[1]
d = mk()
if mode == "direct":
    pass
elif mode == "shutil_first":
    shutil.rmtree(d, ignore_errors=True)
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "[System.IO.Directory]::Delete('%s', $true)" % d],
    capture_output=True, timeout=30)
print("MODE", mode, "rc:", r.returncode, "exists:", os.path.exists(d))
print("stderr:", r.stderr.decode(errors="replace")[:300])
