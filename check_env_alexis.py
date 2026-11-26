"""check_env_alexis.py  --  compare the installed packages with requirements-lock.txt.

    python check_env_alexis.py            # report; exit 0 if every pinned package matches
    python check_env_alexis.py --strict   # also fail on a different Python minor version

The committed result was made in the environment of requirements-lock.txt (Python 3.13).
A different version does not have to change the result (`make check-reproducible` says
whether it did), but this tells a reviewer up front which packages differ.
"""

import argparse
import platform
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "requirements-lock.txt"
REFERENCE_PYTHON = "3.13"


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path=LOCK):
    pins = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            pins[norm(name)] = ver.strip()
    return pins


def compare(pins):
    rows = []
    for name, want in sorted(pins.items()):
        try:
            have = version(name)
        except PackageNotFoundError:
            have = None
        rows.append((name, want, have))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true", help="fail on a different Python minor version")
    a = ap.parse_args(argv)
    rows = compare(read_lock())
    bad = [r for r in rows if r[2] != r[1]]
    py = ".".join(platform.python_version_tuple()[:2])
    print(f"python {platform.python_version()} (reference {REFERENCE_PYTHON})"
          + ("" if py == REFERENCE_PYTHON else "  DIFFERENT"))
    for name, want, have in bad:
        print(f"  {name:<28} locked {want:<14} installed {have or 'MISSING'}")
    print(f"{len(rows) - len(bad)}/{len(rows)} locked packages match"
          + ("" if bad else ": ENVIRONMENT MATCHES requirements-lock.txt"))
    fail = bool(bad) or (a.strict and py != REFERENCE_PYTHON)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
