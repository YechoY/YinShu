"""只跑一个用例（非交付物）：python tmp/run_one.py tests.hub.test_api_http.ApiHttpTest.test_10_..."""
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

name = sys.argv[1] if len(sys.argv) > 1 else \
    "tests.hub.test_api_http.ApiHttpTest.test_10_deletion_not_revived_by_non_deleting_client"
unittest.TextTestRunner(verbosity=2).run(
    unittest.TestLoader().loadTestsFromName(name))
