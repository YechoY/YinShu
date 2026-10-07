"""分步验证：不跑 shutil.rmtree 直接 .NET 删除是否成功。"""
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.abspath("tmp")

def dotnet_delete(d):
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[System.IO.Directory]::Delete('%s', $true)" % d],
        capture_output=True, timeout=30)

# 场景 A：不跑 shutil，直接 .NET 递归删
a = tempfile.mkdtemp(prefix="dbg_a_", dir=ROOT)
with open(os.path.join(a, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(a, "sub"))
with open(os.path.join(a, "sub", "y.txt"), "w") as f:
    f.write("yo")
r = dotnet_delete(a)
print("A(no shutil) rc:", r.returncode, "exists:", os.path.exists(a))

# 场景 B：shutil 假删后再 .NET
b = tempfile.mkdtemp(prefix="dbg_b_", dir=ROOT)
with open(os.path.join(b, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(b, "sub"))
with open(os.path.join(b, "sub", "y.txt"), "w") as f:
    f.write("yo")
shutil.rmtree(b, ignore_errors=True)
print("B after shutil exists:", os.path.exists(b))
r2 = dotnet_delete(b)
print("B(no shutil) rc:", r2.returncode, "exists:", os.path.exists(b))

# 场景 C：shutil 后再 .NET，但列出实际残留
c = tempfile.mkdtemp(prefix="dbg_c_", dir=ROOT)
with open(os.path.join(c, "x.txt"), "w") as f:
    f.write("hi")
os.makedirs(os.path.join(c, "sub"))
with open(os.path.join(c, "sub", "y.txt"), "w") as f:
    f.write("yo")
shutil.rmtree(c, ignore_errors=True)
print("C after shutil exists:", os.path.exists(c))
# 用 PowerShell 列出实际残留
r3 = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-ChildItem -LiteralPath '%s' -Recurse -Force | ForEach-Object { $_.FullName }" % c],
    capture_output=True, timeout=30)
print("C actual remaining:", r3.stdout, "err:", r3.stderr)
