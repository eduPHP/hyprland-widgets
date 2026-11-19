import subprocess
import sys


def test_version():
    out = subprocess.run([sys.executable, "-m", "hyprspot", "--version"], capture_output=True, text=True)
    assert out.stdout.strip() == "hyprspot 0.1.0"
