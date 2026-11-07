"""amzleak 命令行入口。

只做报表读取、既有审计引擎编排与结果呈现，不发起任何网络请求，也不写
``--out`` / ``sample -o`` 之外的路径。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from . import __version__
from . import inventory as inventory_engine
from . import ledger as ledger_engine
from . import search_terms as search_terms_engine
from . import settlement as settlement_engine


HOSTED_LEDGER_URL = "https://data-copilot.whfc.cc/leak-ledger"

_SAMPLE_CONTENT = {
    "settlement": ("settlement-sample.tsv", settlement_engine.sample_report_csv),
    "inventory": ("inventory-age-sample.csv", inventory_engine.sample_report_csv),
    "ppc": ("search-term-sample.csv", search_terms_engine.sample_report_csv),
}

_SEVERITY_LABELS = {"high": "高", "mid": "中", "low": "低"}


class CliError(Exception):
    """可由 CLI 转成中文错误提示的异常。"""


class _ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"错误：{message}\n")


def _as_float(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _money(value: Any, currency: Any) -> str:
    amount = _as_float(value)
    code = str(currency or "unknown").strip().upper() or "UNKNOWN"
    if code == "USD":
        return f"${amount:,.2f}"
    return f"{code} {amount:,.2f}"


def _read_bytes(filename: str) -> bytes:
    path = Path(filename)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise CliError(f"无法读取文件 {filename}：{exc}") from exc
    if not content:
        raise CliError(f"文件为空：{filename}")
    return content


def _write_text(filename: str, content: str, *, bom: bool = False) -> None:
    text = str(content or "")
    if bom:
        if text.startswith("\ufeff"):
            text = text[1:]
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        Path(filename).write_text(text, encoding=encoding, newline="")
    except OSError as exc:
        raise CliError(f"无法写入文件 {filename}：{exc}") from exc


def _emit_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _parse_settlement(content: bytes, filename: str, expected_margin_pct: float) -> Dict[str, Any]:
    parsed = settlement_engine.parse_settlement_report(content, filename)
    report = settlement_engine.audit_settlement(
        parsed["records"],
        expected_margin_pct=max(0.0, float(expected_margin_pct or 0.0)),
    )
    report["rows_total"] = parsed["rows_total"]
    report["rows_seen"] = parsed["rows_seen"]
    report["skipped"] = parsed["skipped"]
    report["columns_mapped"] = parsed["columns"]
    report["warnings"] = list(parsed["warnings"]) + list(report.get("warnings") or [])
    return report


def _parse_inventory(
    content: bytes,
    filename: str,
    unit_cost: float,
    expected_price: float,
) -> Dict[str, Any]:
    parsed = inventory_engine.parse_inventory_report(content, filename)
    report = inventory_engine.audit_inventory(
        parsed["records"],
        unit_cost=max(0.0, float(unit_cost or 0.0)),
        expected_price=max(0.0, float(expected_price or 0.0)),
        currency=parsed.get("currency", "unknown"),
    )
    report["rows_total"] = parsed["rows_total"]
    report["skipped"] = parsed["skipped"]
    report["columns_mapped"] = parsed["columns"]
    report["totals"]["ltsf_source"] = parsed["ltsf_source"]
    report["warnings"] = list(parsed["warnings"]) + list(report.get("warnings") or [])
    return report


def _ppc_report_from_normalized(
    normalized: Dict[str, Any], gross_margin_pct: float
) -> Dict[str, Any]:
    report = search_terms_engine.audit_report(
        normalized["records"],
        gross_margin_pct=max(0.0, float(gross_margin_pct or 0.0)),
        currency=normalized["currency"],
    )
    report["rows_total"] = normalized["rows_total"]
    report["rows_after_aggregate"] = normalized["rows_after_aggregate"]
    report["columns_mapped"] = normalized["columns_mapped"]
    report["warnings"] = list(normalized["warnings"]) + list(report.get("warnings") or [])
    return report


def _parse_ppc(content: bytes, filename: str, gross_margin_pct: float) -> Dict[str, Any]:
    parsed = search_terms_engine.parse_report_bytes(content, filename)
    normalized = search_terms_engine.normalize_and_aggregate(parsed["headers"], parsed["rows"])
    return _ppc_report_from_normalized(normalized, gross_margin_pct)


def _cmd_sample(args: argparse.Namespace) -> int:
    selected = list(_SAMPLE_CONTENT) if args.type == "all" else [args.type]
    output_dir = Path(args.out)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError(f"无法创建目录 {output_dir}：{exc}") from exc

    files: Dict[str, str] = {}
    for report_type in selected:
        filename, sample_fn = _SAMPLE_CONTENT[report_type]
        path = output_dir / filename
        try:
            path.write_text(sample_fn(), encoding="utf-8", newline="")
        except OSError as exc:
            raise CliError(f"无法写入文件 {path}：{exc}") from exc
        files[report_type] = str(path)

    if args.json:
        _emit_json({"type": args.type, "output_dir": str(output_dir), "files": files})
    else:
        print("amzleak · 已生成合成样例报表")
        for report_type in selected:
            print(f"  {report_type:<11} {files[report_type]}")
    return 0


def _render_settlement(report: Dict[str, Any]) -> None:
    currency = report.get("currency", "unknown")
    print("amzleak · 亚马逊结算报表漏损审计（数据不出本机）")
    print()
    print(f"净利率        {_as_float(report.get('net_margin_pct')):.2f}%")
    print(f"评级          {report.get('grade', '')}")
    print()
    print("三类最大漏损")
    drivers = list(report.get("top_fee_drivers") or [])[:3]
    if not drivers:
        print("  暂无异常扣费")
    for index, item in enumerate(drivers, start=1):
        label = str(item.get("label") or item.get("key") or "未分类")
        amount = _money(item.get("amount"), currency)
        share = _as_float(item.get("share_pct"))
        print(f" {index}. {label} {amount}（占销售额 {share:.2f}%）")


def _cmd_settlement(args: argparse.Namespace) -> int:
    report = _parse_settlement(
        _read_bytes(args.file), args.file, args.expected_margin_pct
    )
    if args.out:
        _write_text(args.out, settlement_engine.build_action_csv(report), bom=True)
    if args.json:
        _emit_json(report)
    else:
        _render_settlement(report)
    return 0


def _render_inventory(report: Dict[str, Any]) -> None:
    totals = report.get("totals") or {}
    params = report.get("params") or {}
    currency = params.get("currency", "unknown")
    print("amzleak · 亚马逊库存龄期漏损审计（数据不出本机）")
    print()
    print(f"压死资金          {_money(totals.get('locked_cost'), currency)}")
    print(f"长期仓储费敞口    {_money(totals.get('ltsf_total'), currency)}")
    print(f"90 天可回收现金   {_money(totals.get('recoverable_cash'), currency)}")
    print(f"365+ 建议移除件数 {int(_as_float(totals.get('removal_units')))} 件")


def _cmd_inventory(args: argparse.Namespace) -> int:
    report = _parse_inventory(
        _read_bytes(args.file),
        args.file,
        args.unit_cost,
        args.price,
    )
    if args.out:
        _write_text(args.out, str(report.get("action_csv") or ""), bom=True)
    if args.json:
        _emit_json(report)
    else:
        _render_inventory(report)
    return 0


def _render_ppc(report: Dict[str, Any]) -> None:
    totals = report.get("totals") or {}
    waste = report.get("waste") or {}
    recoverable = report.get("recoverable") or {}
    currency = report.get("currency", "unknown")
    print("amzleak · 亚马逊搜索词空耗审计（数据不出本机）")
    print()
    print(f"广告花费       {_money(totals.get('spend'), currency)}")
    print(
        f"零转化空耗     {_money(waste.get('zero_conversion_spend'), currency)}"
        f"（{int(_as_float(waste.get('zero_conversion_terms')))} 个搜索词）"
    )
    print(
        f"倒挂亏损       {_money(waste.get('negative_margin_loss'), currency)}"
        f"（{int(_as_float(waste.get('negative_margin_terms')))} 个搜索词）"
    )
    print(f"保守可回收     {_money(recoverable.get('conservative_total'), currency)}")


def _cmd_ppc(args: argparse.Namespace) -> int:
    parsed = search_terms_engine.parse_report_bytes(_read_bytes(args.file), args.file)
    normalized = search_terms_engine.normalize_and_aggregate(parsed["headers"], parsed["rows"])
    report = _ppc_report_from_normalized(normalized, args.gross_margin_pct)
    negatives = search_terms_engine.build_negatives(
        normalized["records"], min_spend=args.min_spend
    )
    report["negatives_count"] = int(negatives["count"])
    report["negatives_truncated"] = bool(negatives["truncated"])
    report["negatives_txt"] = str(negatives["txt"])
    report["negatives_csv"] = str(negatives["csv"])
    if args.out:
        _write_text(args.out, str(negatives["txt"]), bom=True)
    if args.json:
        _emit_json(report)
    else:
        _render_ppc(report)
    return 0


def _severity_for_action(action: Dict[str, Any], items: Sequence[Dict[str, Any]]) -> str:
    for item in items:
        if (
            item.get("source") == action.get("source")
            and item.get("title") == action.get("title")
            and math.isclose(
                _as_float(item.get("amount")),
                _as_float(action.get("amount")),
                abs_tol=0.005,
            )
        ):
            return str(item.get("severity") or "low")
    return "low"


def _render_ledger(ledger: Dict[str, Any]) -> None:
    headline = ledger.get("headline") or {}
    actions = list(ledger.get("top_actions") or [])
    items = list(ledger.get("items") or [])
    currency = ledger.get("currency", "unknown")

    print("amzleak · 亚马逊报表本地漏损审计（数据不出本机）")
    print()
    print(
        f"可追回现金   {_money(headline.get('recoverable_cash'), currency):<14}"
        " ← 本周期白名单异常 + 广告保守可回收"
    )
    print(
        f"被压死资金   {_money(headline.get('locked_capital'), currency):<14}"
        " ← 存量，不可与上一行相加"
    )
    print(f"已流失难追回 {_money(headline.get('lost_cash'), currency)}")
    print()
    print(f"今天最该做的 {len(actions)} 件事")
    if not actions:
        print("  暂无动作")
    for action in actions:
        severity = _SEVERITY_LABELS.get(
            _severity_for_action(action, items), "低"
        )
        print(
            f" {int(action.get('rank') or 0)}. [{severity}] {action.get('title', '')} "
            f"{_money(action.get('amount'), currency)} —— {action.get('action', '')}"
        )
    print()
    print(f"📄 完整三份报表合并视图（浏览器里看，免注册）：{HOSTED_LEDGER_URL}")


def _cmd_ledger(args: argparse.Namespace) -> int:
    if not any((args.settlement, args.inventory, args.ppc)):
        print(ledger_engine.EMPTY_LEDGER_MESSAGE, file=sys.stderr)
        return 3

    reports: Dict[str, Dict[str, Any]] = {}
    if args.settlement:
        reports["settlement"] = _parse_settlement(
            _read_bytes(args.settlement), args.settlement, args.expected_margin_pct
        )
    if args.inventory:
        reports["inventory"] = _parse_inventory(
            _read_bytes(args.inventory),
            args.inventory,
            args.unit_cost,
            args.price,
        )
    if args.ppc:
        reports["ppc"] = _parse_ppc(
            _read_bytes(args.ppc), args.ppc, args.gross_margin_pct
        )

    ledger = ledger_engine.build_ledger(
        settlement=reports.get("settlement"),
        inventory=reports.get("inventory"),
        ppc=reports.get("ppc"),
    )
    if args.json:
        _emit_json(ledger)
    else:
        _render_ledger(ledger)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = _ChineseArgumentParser(
        prog="amzleak",
        description="亚马逊报表本地漏损审计：结算、库龄、搜索词三表合并总账。",
    )
    parser.add_argument("--version", action="version", version=f"amzleak {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="命令")

    sample = subparsers.add_parser("sample", help="生成合成样例报表")
    sample.add_argument(
        "--type",
        choices=("settlement", "inventory", "ppc", "all"),
        default="all",
        help="样例类型（默认 all）",
    )
    sample.add_argument("-o", "--out", default=".", metavar="DIR", help="输出目录（默认当前目录）")
    sample.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    sample.set_defaults(handler=_cmd_sample)

    settlement = subparsers.add_parser("settlement", help="审计亚马逊结算报表")
    settlement.add_argument("file", metavar="FILE")
    settlement.add_argument("--expected-margin-pct", type=float, default=25.0)
    settlement.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    settlement.add_argument("--out", metavar="actions.csv", help="写出动作 CSV")
    settlement.set_defaults(handler=_cmd_settlement)

    inventory = subparsers.add_parser("inventory", help="审计亚马逊库存龄期报表")
    inventory.add_argument("file", metavar="FILE")
    inventory.add_argument("--unit-cost", type=float, default=8.0)
    inventory.add_argument("--price", type=float, default=29.9)
    inventory.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    inventory.add_argument("--out", metavar="clear.csv", help="写出清货动作 CSV")
    inventory.set_defaults(handler=_cmd_inventory)

    ppc = subparsers.add_parser("ppc", help="审计亚马逊搜索词报表")
    ppc.add_argument("file", metavar="FILE")
    ppc.add_argument("--gross-margin-pct", type=float, default=30.0)
    ppc.add_argument("--min-spend", type=float, default=1.0)
    ppc.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ppc.add_argument("--out", metavar="negatives.tsv", help="写出批量否定词 TSV")
    ppc.set_defaults(handler=_cmd_ppc)

    ledger = subparsers.add_parser("ledger", help="合并三份报表生成漏钱总账")
    ledger.add_argument("--settlement", metavar="F")
    ledger.add_argument("--inventory", metavar="F")
    ledger.add_argument("--ppc", metavar="F")
    ledger.add_argument("--unit-cost", type=float, default=8.0)
    ledger.add_argument("--price", type=float, default=29.9)
    ledger.add_argument("--expected-margin-pct", type=float, default=25.0)
    ledger.add_argument("--gross-margin-pct", type=float, default=30.0)
    ledger.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ledger.set_defaults(handler=_cmd_ledger)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except CliError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
