#!/usr/bin/env python3
"""Check that organ.py conforms to the orchestrator CONTRACT on all samples.

Every organ proves itself before the orchestrator trusts it: its decide() must
emit {output, rationale, self_metric} with a numeric self_metric.confidence in
[0.0, 1.0] on every sample AND on empty state, with no error key. A brick that
drifts from the contract goes RED here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(input_value):
    env = os.environ.copy()
    env["ORGAN_INPUT"] = input_value
    return subprocess.run(
        ["python3", "organ.py"], env=env, capture_output=True, text=True
    )


def _check_contract(data, where):
    if not isinstance(data, dict):
        print(f"::error::Top-level output is not a JSON object on {where}")
        return False
    for key in ("output", "rationale", "self_metric"):
        if key not in data:
            print(f"::error::Contract violation on {where}: missing {key!r}")
            return False
    metric = data["self_metric"]
    if not isinstance(metric, dict):
        print(f"::error::Contract violation on {where}: self_metric is not a dict")
        return False
    if "confidence" not in metric:
        print(f"::error::Contract violation on {where}: self_metric.confidence is required")
        return False
    conf = metric["confidence"]
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        print(f"::error::Contract violation on {where}: confidence is not numeric")
        return False
    if not (0.0 <= conf <= 1.0):
        print(f"::error::Contract violation on {where}: confidence {conf} out of [0.0, 1.0]")
        return False
    if "error" in metric:
        print(f"::error::Contract violation on {where}: self_metric has 'error' key")
        return False
    return True


def _check_payload(input_value, where):
    result = _run(input_value)
    if result.returncode != 0:
        print(f"::error::organ.py exited {result.returncode} on {where}")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        return False
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"::error::Invalid JSON output on {where}: {exc}")
        print("Output:", result.stdout)
        return False
    if not _check_contract(data, where):
        return False
    print(f"✓ Contract OK: {where}")
    return True


def main():
    if not Path("organ.py").exists():
        print("::error::no top-level organ.py — this is not a contract-conforming organ")
        sys.exit(1)

    all_ok = True

    # Empty state must fail safe, not crash.
    all_ok &= _check_payload('{"state":{}}', "empty state")

    samples = sorted(Path("samples").glob("*.json"))
    if not samples:
        print("::warning::no samples/*.json to contract-check")
    for sample in samples:
        all_ok &= _check_payload(str(sample), str(sample))

    if not all_ok:
        sys.exit(1)
    print("\nAll contract checks passed.")


if __name__ == "__main__":
    main()
