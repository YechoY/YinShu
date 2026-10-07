import os
import subprocess
import tempfile
import time

ROOT = os.path.abspath("tmp")

d = tempfile.mkdtemp(prefix="dbg_view_", dir=ROOT)
fp = os.path.join(d, "y.txt")
with open(fp, "w") as f:
    f.write("yo")

def test_path(p):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Test-Path -LiteralPath '{p}'"],
        capture_output=True, timeout=10)
    return r.stdout.strip() == b"True"

print("before: os.path.exists:", os.path.exists(fp), "Test-Path:", test_path(fp))

# Python os.remove
os.remove(fp)
print("after os.remove: os.path.exists:", os.path.exists(fp), "Test-Path:", test_path(fp))

# 再等 2s 看最终状态
time.sleep(2)
print("after 2s: os.path.exists:", os.path.exists(fp), "Test-Path:", test_path(fp))

# 用 .NET File.Delete（进程外）
fp2 = os.path.join(d, "z.txt")
with open(fp2, "w") as f:
    f.write("yo")
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     f"[System.IO.File]::Delete('{fp2}')"],
    capture_output=True, timeout=10)
print("dotnet File.Delete rc:", r.returncode, "exists:", os.path.exists(fp2), "Test-Path:", test_path(fp2))
time.sleep(1)
print("dotnet after 1s: exists:", os.path.exists(fp2), "Test-Path:", test_path(fp2))
