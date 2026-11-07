from __future__ import annotations

import json
from pathlib import Path

from test_cli import make_samples, run_cli


def test_three_samples_match_online_demo_numbers(tmp_path: Path) -> None:
    demo = make_samples(tmp_path)
    result = run_cli(
        tmp_path,
        "ledger",
        "--settlement",
        str(demo / "settlement-sample.tsv"),
        "--inventory",
        str(demo / "inventory-age-sample.csv"),
        "--ppc",
        str(demo / "search-term-sample.csv"),
        "--json",
    )
    assert result.returncode == 0, result.stderr
    ledger = json.loads(result.stdout)

    assert ledger["headline"]["recoverable_cash"] == 860.26
    assert ledger["headline"]["locked_capital"] == 10920.0
    assert ledger["headline"]["lost_cash"] == 1503.5

    assert ledger["coverage"]["reports"] == 3
    assert ledger["coverage"]["total"] == 3
    assert [action["rank"] for action in ledger["top_actions"]] == [1, 2, 3]
    assert ledger["top_actions"][0]["title"] == "疑似重复扣费"
    assert ledger["top_actions"][0]["amount"] == 120.0
    assert ledger["currency"] == "USD"
