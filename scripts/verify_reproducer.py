#!/usr/bin/env python
"""Calibrate the reproducer sandbox — the inner-loop reward engine — offline.

Mirror of the step-1 harness calibration, but for the agent's OWN signal
instead of the graded tests, and with NO LLM: we hand-write a reproducer for
django__django-11099 and run it in the container two ways.

    empty patch -> reproducer RED   (the bug still reproduces)
    gold patch  -> reproducer GREEN (the bug behavior is gone)

If this holds, the execution half of the replan loop is trustworthy: a patch
that fixes the issue makes the reproducer pass, and one that doesn't makes it
fail. The model half (writing the reproducer, replanning) plugs in on top.

    python scripts/verify_reproducer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swebench.harness.test_spec.test_spec import make_test_spec

from fixpoint.bench import get, load_lite
from fixpoint.harness.sandbox import run_reproducer

# A reproducer written the way the agent will write one: assert the FIXED
# behavior, exit non-zero if the bug is present. Configures minimal Django
# settings so importing the validators needs no project on disk.
REPRODUCER = r"""
import sys
from django.conf import settings
if not settings.configured:
    settings.configure(USE_I18N=False)
from django.contrib.auth import validators
from django.core.exceptions import ValidationError

def rejects(validator, value):
    try:
        validator(value)
        return False  # accepted the bad value -> bug present
    except ValidationError:
        return True

ascii_v = validators.ASCIIUsernameValidator()
unicode_v = validators.UnicodeUsernameValidator()
# The fix makes both reject a trailing newline.
fixed = rejects(ascii_v, "foo\n") and rejects(unicode_v, "foo\n")
print("ascii rejects newline:", rejects(ascii_v, "foo\n"))
print("unicode rejects newline:", rejects(unicode_v, "foo\n"))
sys.exit(0 if fixed else 1)
"""


def image_for(instance_id: str) -> tuple[str, str]:
    inst = get(load_lite(), instance_id)
    d = {"instance_id": inst.instance_id, "repo": inst.repo, "base_commit": inst.base_commit,
         "patch": "", "test_patch": "", "problem_statement": "", "hints_text": "",
         "created_at": inst.created_at, "version": inst.version,
         "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]",
         "environment_setup_commit": inst.environment_setup_commit}
    return make_test_spec(d, namespace="swebench").instance_image_key, inst.base_commit


def main() -> int:
    iid = "django__django-11099"
    image, base = image_for(iid)
    gold = (Path(__file__).resolve().parent.parent / "data" / "step0" / iid / "gold_patch.diff").read_text()

    print(f"reproducer calibration — {iid}\n  image: {image}\n")
    red = run_reproducer(image, base, patch="", script=REPRODUCER)
    green = run_reproducer(image, base, patch=gold, script=REPRODUCER)

    print(f"empty patch : applied={red.applied} exit={red.exit_code} -> "
          f"{'GREEN' if red.green else 'RED'}  (want RED)")
    print(f"gold patch  : applied={green.applied} exit={green.exit_code} -> "
          f"{'GREEN' if green.green else 'RED'}  (want GREEN)")

    ok = (not red.green) and green.green and green.applied
    if not ok:
        print("\n--- gold-run output (for debugging) ---")
        print(green.output[-1500:])
    print(f"\nreproducer sandbox: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
