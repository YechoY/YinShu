"""临时排查脚本（非交付物）：只跑单个测试模块/用例，复现全量跑时的失败。

用法：
  .venv\\Scripts\\python.exe tmp\\run_only.py tests.hub.test_hub
  .venv\\Scripts\\python.exe tmp\\run_only.py tests.hub.test_hub.SmokeTest.test_04_cyshine_put_get_roundtrip
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
TMP = os.path.join(ROOT, "tmp")


def mkdtemp(prefix=None, suffix=None, dir=None):
    base = dir or TMP
    name = (prefix or "tmp") + next(tempfile._get_candidate_names())
    path = os.path.join(base, name)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Directory -Force -Path '{path}' | Out-Null"],
                   capture_output=True, timeout=30)
    return path


tempfile.mkdtemp = mkdtemp
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

names = sys.argv[1].split(",") if len(sys.argv) > 1 else ["tests.hub.test_hub"]
target = ",".join(names)
loader = unittest.TestLoader()
suite = unittest.TestSuite()
for n in names:
    suite.addTests(loader.loadTestsFromName(n))
res = unittest.TextTestRunner(verbosity=2).run(suite)
print("=" * 60)
print(f"target={target} tests={res.testsRun} failures={len(res.failures)} "
      f"errors={len(res.errors)} -> {'ALL PASS' if res.wasSuccessful() else 'HAS FAILURES'}")
