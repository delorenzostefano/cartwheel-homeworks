"""Homework 9 Part D, step 3: check the cascade once on the HW5 test labels."""

from __future__ import annotations

import json
from pathlib import Path

from cascade.thresholds import cascade_row, latest_human_labels, oracle_verdicts, read_proxy

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_AGREEMENT = 0.95  # set before any result was seen
# On the development labels no proxy threshold met the requirement, so the
# chosen setting is 1.01 (oracle only) and the cascade is rejected. The best
# proxy threshold, 0.99, is checked on the test labels for information only.
CHOSEN_THRESHOLD = 1.01
INFORMATIONAL_THRESHOLD = 0.99


def main() -> None:
    proxy = read_proxy("test")
    oracle = oracle_verdicts()
    human = latest_human_labels()
    result = {"required_agreement_with_oracle": REQUIRED_AGREEMENT, "traces": len(proxy)}
    for name, threshold in (("chosen", CHOSEN_THRESHOLD), ("informational", INFORMATIONAL_THRESHOLD)):
        row = cascade_row(threshold, proxy, oracle, human)
        row["disagreements_with_oracle"] = round((1 - row["agreement_with_oracle"]) * len(proxy))
        row["meets_requirement"] = row["agreement_with_oracle"] >= REQUIRED_AGREEMENT
        result[name] = row
    result["decision"] = "rejected: no proxy threshold met the agreement requirement on the development labels"
    out = ROOT / "cascade/results/check.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"Saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
