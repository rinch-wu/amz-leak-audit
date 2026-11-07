from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from amzleak import inventory, search_terms, settlement


ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "amzleak", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def make_samples(tmp_path: Path) -> Path:
    result = run_cli(tmp_path, "sample", "-o", "demo")
    assert result.returncode == 0, result.stderr
    return tmp_path / "demo"


def test_sample_writes_unchanged_engine_outputs_and_json(tmp_path: Path) -> None:
    demo = make_samples(tmp_path)
    expected = {
        "settlement-sample.tsv": settlement.sample_report_csv(),
        "inventory-age-sample.csv": inventory.sample_report_csv(),
        "search-term-sample.csv": search_terms.sample_report_csv(),
    }
    for filename, content in expected.items():
        assert (demo / filename).read_text(encoding="utf-8") == content

    result = run_cli(tmp_path, "sample", "--type", "ppc", "-o", "one", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["type"] == "ppc"
    assert set(payload["files"]) == {"ppc"}
    assert (tmp_path / "one" / "search-term-sample.csv").exists()


def test_settlement_json_human_and_action_csv(tmp_path: Path) -> None:
    demo = make_samples(tmp_path)
    report_file = demo / "settlement-sample.tsv"
    result = run_cli(tmp_path, "settlement", str(report_file), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["net_margin_pct"] == 40.45
    assert payload["grade"] == "C"
    assert payload["rows_total"] == 78
    assert payload["columns_mapped"]["amount"] == "amount"

    human = run_cli(tmp_path, "settlement", str(report_file))
    assert human.returncode == 0, human.stderr
    assert "净利率" in human.stdout
    assert "评级" in human.stdout
    assert "三类最大漏损" in human.stdout

    out_file = tmp_path / "actions.csv"
    written = run_cli(tmp_path, "settlement", str(report_file), "--out", str(out_file))
    assert written.returncode == 0, written.stderr
    raw = out_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert not raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    assert "row_type" in out_file.read_text(encoding="utf-8-sig")


def test_inventory_json_human_and_clear_csv(tmp_path: Path) -> None:
    demo = make_samples(tmp_path)
    report_file = demo / "inventory-age-sample.csv"
    result = run_cli(tmp_path, "inventory", str(report_file), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["totals"]["locked_cost"] == 10920.0
    assert payload["totals"]["ltsf_total"] == 505.6
    assert payload["totals"]["recoverable_cash"] == 16052.11
    assert payload["totals"]["removal_units"] == 90

    human = run_cli(
        tmp_path,
        "inventory",
        str(report_file),
        "--unit-cost",
        "10",
        "--price",
        "20",
    )
    assert human.returncode == 0, human.stderr
    assert "压死资金" in human.stdout
    assert "长期仓储费敞口" in human.stdout
    assert "90 天可回收现金" in human.stdout
    assert "365+ 建议移除件数" in human.stdout

    out_file = tmp_path / "clear.csv"
    written = run_cli(tmp_path, "inventory", str(report_file), "--out", str(out_file))
    assert written.returncode == 0, written.stderr
    raw = out_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert not raw.startswith(b"\xef\xbb\xbf\xef\xbb\xbf")
    assert "ASIN" in out_file.read_text(encoding="utf-8-sig")


def test_ppc_json_human_and_negative_tsv(tmp_path: Path) -> None:
    demo = make_samples(tmp_path)
    report_file = demo / "search-term-sample.csv"
    result = run_cli(tmp_path, "ppc", str(report_file), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["totals"]["spend"] == 345.6
    assert payload["waste"]["zero_conversion_spend"] == 115.9
    assert payload["waste"]["negative_margin_loss"] == 8.5
    assert payload["recoverable"]["conservative_total"] == 110.26
    assert payload["rows_total"] == 60

    human = run_cli(tmp_path, "ppc", str(report_file))
    assert human.returncode == 0, human.stderr
    for label in ("广告花费", "零转化空耗", "倒挂亏损", "保守可回收"):
        assert label in human.stdout

    out_file = tmp_path / "negatives.tsv"
    written = run_cli(
        tmp_path,
        "ppc",
        str(report_file),
        "--min-spend",
        "10",
        "--out",
        str(out_file),
        "--json",
    )
    assert written.returncode == 0, written.stderr
    payload = json.loads(written.stdout)
    assert payload["negatives_count"] == 6
    raw = out_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = out_file.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 6
    assert lines[0].split("\t")[5] == "wireless earbuds"


def test_ledger_human_output_and_single_report(tmp_path: Path) -> None:
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
    )
    assert result.returncode == 0, result.stderr
    assert "数据不出本机" in result.stdout
    assert "$860.26" in result.stdout
    assert "$10,920.00" in result.stdout
    assert "$1,503.50" in result.stdout
    assert "今天最该做的 3 件事" in result.stdout
    assert "[高] 疑似重复扣费 $120.00" in result.stdout
    assert "https://data-copilot.whfc.cc/leak-ledger" in result.stdout

    single = run_cli(
        tmp_path,
        "ledger",
        "--settlement",
        str(demo / "settlement-sample.tsv"),
        "--json",
    )
    assert single.returncode == 0, single.stderr
    payload = json.loads(single.stdout)
    assert payload["coverage"]["reports"] == 1
    assert payload["headline"]["locked_capital"] == 0.0


def test_help_and_error_exit_codes(tmp_path: Path) -> None:
    help_result = run_cli(tmp_path, "--help")
    assert help_result.returncode == 0
    for command in ("sample", "settlement", "inventory", "ppc", "ledger"):
        assert command in help_result.stdout

    no_reports = run_cli(tmp_path, "ledger")
    assert no_reports.returncode == 3
    assert "请至少" in no_reports.stderr

    bad_choice = run_cli(tmp_path, "sample", "--type", "unknown")
    assert bad_choice.returncode == 2
    assert "错误：" in bad_choice.stderr

    missing = run_cli(tmp_path, "settlement", "missing.tsv")
    assert missing.returncode == 2
    assert "无法读取文件" in missing.stderr

    broken = tmp_path / "broken.csv"
    broken.write_text("hello\nworld\n", encoding="utf-8")
    parse_error = run_cli(tmp_path, "ppc", str(broken))
    assert parse_error.returncode == 2
    assert "错误：" in parse_error.stderr
