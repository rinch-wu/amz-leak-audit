# -*- coding: utf-8 -*-
"""Feature 276: 店铺漏钱总账（Leak Ledger）聚合引擎。

本模块只做聚合与口径映射，不复制任何报表解析或审计逻辑。三个输入都是既有
审计引擎已经算好的 report dict：

- ``settlement_profit_audit.audit_settlement``
- ``inventory_age_audit.audit_inventory``
- ``ppc_search_term_audit.audit_report``

输出严格区分：

1. ``recoverable_cash``：本周期可追回现金（流量口径）
2. ``locked_capital``：被压死的库存资金（存量口径，不能与 1 相加）
3. ``lost_cash``：本周期已流失、难追回的钱
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


EMPTY_LEDGER_MESSAGE = "请至少上传一份报表，或点「一键加载三份样例报表」试玩。"

_SOURCE_LABELS = {
    "settlement": "结算漏损",
    "inventory": "库存压死",
    "ppc": "广告空耗",
}

_SOURCE_ORDER = ("settlement", "inventory", "ppc")

_TOOL_URLS = {
    "settlement": "/tools/settlement-profit-audit",
    "inventory": "/tools/inventory-age-audit",
    "ppc": "/tools/ppc-search-term-audit",
}

_SEVERITY_RANK = {"high": 0, "mid": 1, "low": 2}

_RECOVERABLE_ANOMALY_CODES = {
    "duplicate_fee",
    "unreconciled",
    "fee_on_refund",
    "commission_outlier",
}


def _number(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _amount(value: Any) -> float:
    return round(_number(value), 2)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _currency(value: Any) -> str:
    currency = _text(value).upper()
    return currency or "unknown"


def _source_currency(source: str, report: Dict[str, Any]) -> str:
    """读取来源币种；兼容被测代码直接写入 report["currency"] 的用法。"""
    direct = _currency(report.get("currency"))
    if direct != "unknown":
        return direct
    if source == "inventory":
        params = report.get("params")
        if isinstance(params, dict):
            nested = _currency(params.get("currency"))
            if nested != "unknown":
                return nested
    return "unknown"


def _known_currency(currency: str) -> bool:
    return bool(currency and currency.upper() not in {"UNKNOWN", "MIXED"})


def _money(value: Any, currency: str) -> str:
    amount = _amount(value)
    label = currency if _known_currency(currency) else "原币"
    return f"{label} {amount:,.2f}"


def _item(
    *,
    source: str,
    title: str,
    amount: Any,
    bucket: str,
    severity: str,
    evidence: str,
    action: str,
    currency: str,
    currency_included: bool,
    base_currency: str,
) -> Dict[str, Any]:
    evidence_text = evidence
    if not currency_included:
        evidence_text = (
            f"（{currency if _known_currency(currency) else '该币种'}报表未并入 "
            f"{base_currency} 总账）{evidence_text}"
        )
    return {
        "source": source,
        "source_label": _SOURCE_LABELS[source],
        "title": title,
        "amount": _amount(amount),
        "bucket": bucket,
        "severity": severity if severity in _SEVERITY_RANK else "low",
        "evidence": evidence_text,
        "action": action,
        "tool_url": _TOOL_URLS[source],
        "currency": currency,
    }


def _ppc_items(
    report: Dict[str, Any],
    currency: str,
    currency_included: bool,
    base_currency: str,
) -> List[Dict[str, Any]]:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    waste = report.get("waste") if isinstance(report.get("waste"), dict) else {}
    recoverable = report.get("recoverable") if isinstance(report.get("recoverable"), dict) else {}

    spend = _amount(totals.get("spend"))
    zero_spend = _amount(waste.get("zero_conversion_spend"))
    zero_terms = int(_number(waste.get("zero_conversion_terms")))
    negative_loss = _amount(waste.get("negative_margin_loss"))
    negative_terms = int(_number(waste.get("negative_margin_terms")))
    waste_ratio = _number(waste.get("waste_ratio"))
    conservative_total = _amount(recoverable.get("conservative_total"))
    immediate = _amount(recoverable.get("immediate")) or _amount(zero_spend * 0.9)

    items: List[Dict[str, Any]] = []
    if zero_spend > 0:
        items.append(
            _item(
                source="ppc",
                title="零转化搜索词空烧",
                amount=immediate,
                bucket="recoverable",
                severity="high",
                evidence=(
                    f"{zero_terms} 个搜索词有花费 0 订单，空烧 {_money(zero_spend, currency)}；"
                    f"保留 10% 品牌防守词后，保守可追回 {_money(immediate, currency)}。"
                ),
                action="下载否定关键词清单，先把高花费零转化词加 Negative Exact。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    if negative_loss > 0:
        items.append(
            _item(
                source="ppc",
                title="亏本订单广告损失",
                amount=negative_loss,
                bucket="lost",
                severity="mid",
                evidence=(
                    f"{negative_terms} 个词 ACOS 高于保本线，已倒挂 {_money(negative_loss, currency)}，"
                    "继续放量只会扩大亏损。"
                ),
                action="对倒挂词先降价 30% 或加否定，再按新的保本 ACOS 重新放量。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    if waste_ratio > 0.3 and conservative_total > 0:
        items.append(
            _item(
                source="ppc",
                title="广告花费浪费占比过高",
                amount=conservative_total,
                bucket="recoverable",
                severity="mid",
                evidence=(
                    f"广告花费 {_money(spend, currency)}，浪费占比 {waste_ratio * 100:.2f}%，"
                    f"保守可追回 {_money(conservative_total, currency)}。"
                ),
                action="按「零转化词 → 倒挂词」顺序清理，保留验证过的金牛词预算。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    return items


def _settlement_items(
    report: Dict[str, Any],
    currency: str,
    currency_included: bool,
    base_currency: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    refund_total = _amount(report.get("refund_total"))
    refund_rate = _number(report.get("refund_rate"))
    fee_total = _amount(report.get("fee_total"))
    gross_sales = _amount(report.get("gross_sales"))
    fee_rate = (fee_total / gross_sales * 100.0) if gross_sales > 0 else 0.0

    items: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    if refund_total > 0:
        severity = "high" if refund_rate > 15 else ("mid" if refund_rate > 8 else "low")
        items.append(
            _item(
                source="settlement",
                title="退款吃掉净入账",
                amount=refund_total,
                bucket="lost",
                severity=severity,
                evidence=(
                    f"退款合计 {_money(refund_total, currency)}，退款率 {refund_rate:.2f}%，"
                    "这部分已从本期净入账扣除。"
                ),
                action="按退款率最高的 SKU 逐条查原因，先修尺寸、质量与描述不符。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    if fee_total > 0:
        items.append(
            _item(
                source="settlement",
                title="平台与履约费用侵蚀",
                amount=fee_total,
                bucket="lost",
                severity="low",
                evidence=(
                    f"平台与履约费用合计 {_money(fee_total, currency)}，"
                    f"占商品销售额 {fee_rate:.2f}%。"
                ),
                action="下载费用拆分，优先核对佣金率异常、FBA 附加费与订阅费。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )

    seen_titles = {item["title"] for item in items}
    for index, anomaly in enumerate(report.get("anomalies") or [], start=1):
        if not isinstance(anomaly, dict):
            continue
        amount = abs(_amount(anomaly.get("amount")))
        if amount <= 0:
            continue
        title = _text(anomaly.get("title")) or f"结算异常 #{index}"
        code = _text(anomaly.get("code")).strip().lower()
        detail = _text(anomaly.get("detail")) or f"引擎识别到 {_money(amount, currency)} 的异常。"
        if code not in _RECOVERABLE_ANOMALY_CODES:
            signals.append(
                {
                    "source": "settlement",
                    "title": title,
                    "detail": detail,
                    "amount": amount,
                }
            )
            continue
        if title in seen_titles:
            title = f"{title}（{code or index}）"
        seen_titles.add(title)
        items.append(
            _item(
                source="settlement",
                title=title,
                amount=amount,
                bucket="recoverable",
                severity=_text(anomaly.get("severity")) or "mid",
                evidence=detail,
                action="按异常明细回 Seller Central 对账，确认后向平台开 case 或修正定价。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    return items, signals


def _inventory_items(
    report: Dict[str, Any],
    currency: str,
    currency_included: bool,
    base_currency: str,
) -> List[Dict[str, Any]]:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    locked_cost = _amount(totals.get("locked_cost"))
    locked_ratio = _number(totals.get("locked_ratio"))
    aged_units = int(_number(totals.get("aged_units")))
    ltsf_total = _amount(totals.get("ltsf_total"))
    removal_units = int(_number(totals.get("removal_units")))
    recoverable_cash = _amount(totals.get("recoverable_cash"))

    items: List[Dict[str, Any]] = []
    if locked_cost > 0:
        severity = "high" if locked_ratio > 0.30 else ("mid" if locked_ratio > 0.15 else "low")
        items.append(
            _item(
                source="inventory",
                title="超龄库存压死资金",
                amount=locked_cost,
                bucket="locked",
                severity=severity,
                evidence=(
                    f"{aged_units} 件库存超过 90 天，按单件成本压住 {_money(locked_cost, currency)}，"
                    f"占库存成本 {locked_ratio * 100:.2f}%。这是存量资金占用，不是本期流失。"
                ),
                action="按 365+、271-365、181-270、91-180 天顺序清货或移除。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    if ltsf_total > 0:
        items.append(
            _item(
                source="inventory",
                title="长期仓储费敞口",
                amount=ltsf_total,
                bucket="lost",
                severity="mid",
                evidence=(
                    f"报表长期仓储费合计 {_money(ltsf_total, currency)}，"
                    "继续拖下去会按月重复发生。"
                ),
                action="先处理 365+ 与 271-365 天库存，避免下一期 LTSF 继续累积。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    if removal_units > 0 and recoverable_cash > 0:
        items.append(
            _item(
                source="inventory",
                title="365+ 清货可解锁现金",
                amount=recoverable_cash,
                bucket="locked",
                severity="mid",
                evidence=(
                    f"{removal_units} 件 365+ 库存需移除，全部超龄库存预计可回收 "
                    f"{_money(recoverable_cash, currency)}。按清货假设净回款估算，"
                    "属于存量库存变现，不计入本周期可追回现金。"
                ),
                action="对 365+ 库存先移除，其余按建议折扣清货并复算回款。",
                currency=currency,
                currency_included=currency_included,
                base_currency=base_currency,
            )
        )
    return items


def _metrics(
    settlement: Optional[Dict[str, Any]],
    inventory: Optional[Dict[str, Any]],
    ppc: Optional[Dict[str, Any]],
    headline: Dict[str, float],
    currencies: Dict[str, str],
    included: Dict[str, bool],
) -> List[Dict[str, Any]]:
    settlement = settlement or {}
    inventory = inventory or {}
    ppc = ppc or {}
    ppc_totals = ppc.get("totals") if isinstance(ppc.get("totals"), dict) else {}
    ppc_waste = ppc.get("waste") if isinstance(ppc.get("waste"), dict) else {}
    ppc_recoverable = (
        ppc.get("recoverable") if isinstance(ppc.get("recoverable"), dict) else {}
    )
    inventory_totals = (
        inventory.get("totals") if isinstance(inventory.get("totals"), dict) else {}
    )

    def source_state(source: str) -> str:
        if source not in currencies:
            return "未上传"
        if included.get(source, True):
            return _SOURCE_LABELS[source]
        return f"{currencies[source]} 未并入"

    metrics = [
        {
            "key": "recoverable_cash",
            "label": "本周期可追回现金",
            "value": _amount(headline["recoverable_cash"]),
            "unit": "currency",
            "tone": "good",
            "source": "漏钱总账",
        },
        {
            "key": "locked_capital",
            "label": "被压死的资金（存量）",
            "value": _amount(headline["locked_capital"]),
            "unit": "currency",
            "tone": "warn",
            "source": source_state("inventory"),
        },
        {
            "key": "lost_cash",
            "label": "已流失难追回（本期）",
            "value": _amount(headline["lost_cash"]),
            "unit": "currency",
            "tone": "bad",
            "source": "漏钱总账",
        },
        {
            "key": "ppc_spend",
            "label": "广告花费",
            "value": _amount(ppc_totals.get("spend")),
            "unit": "currency",
            "tone": "neutral",
            "source": source_state("ppc"),
        },
        {
            "key": "zero_conversion_spend",
            "label": "零转化空烧",
            "value": _amount(ppc_waste.get("zero_conversion_spend")),
            "unit": "currency",
            "tone": "bad",
            "source": source_state("ppc"),
        },
        {
            "key": "ppc_conservative_total",
            "label": "广告保守可回收",
            "value": _amount(ppc_recoverable.get("conservative_total")),
            "unit": "currency",
            "tone": "good",
            "source": source_state("ppc"),
        },
        {
            "key": "settlement_gross_sales",
            "label": "结算商品销售额",
            "value": _amount(settlement.get("gross_sales")),
            "unit": "currency",
            "tone": "neutral",
            "source": source_state("settlement"),
        },
        {
            "key": "refund_rate",
            "label": "退款率",
            "value": _amount(settlement.get("refund_rate")),
            "unit": "%",
            "tone": "bad",
            "source": source_state("settlement"),
        },
        {
            "key": "net_margin_pct",
            "label": "真实净利率",
            "value": _amount(settlement.get("net_margin_pct")),
            "unit": "%",
            "tone": "neutral",
            "source": source_state("settlement"),
        },
        {
            "key": "inventory_locked_ratio",
            "label": "压死资金占库存成本",
            "value": _amount(_number(inventory_totals.get("locked_ratio")) * 100.0),
            "unit": "%",
            "tone": "warn",
            "source": source_state("inventory"),
        },
    ]

    inventory_unlock_cash = inventory_totals.get("recoverable_cash")
    if inventory_unlock_cash is not None:
        metrics.insert(
            3,
            {
                "key": "inventory_unlock_cash",
                "label": "清货可解锁现金（存量变现）",
                "value": _amount(inventory_unlock_cash),
                "unit": "money",
                "tone": "locked",
                "source": "inventory",
            },
        )
    return metrics


def build_ledger(
    settlement: Optional[Dict[str, Any]] = None,
    inventory: Optional[Dict[str, Any]] = None,
    ppc: Optional[Dict[str, Any]] = None,
) -> dict:
    """把三份审计结果拼成一张可执行漏钱总账。"""
    reports: Dict[str, Optional[Dict[str, Any]]] = {
        "settlement": settlement if isinstance(settlement, dict) else None,
        "inventory": inventory if isinstance(inventory, dict) else None,
        "ppc": ppc if isinstance(ppc, dict) else None,
    }
    present = [source for source in _SOURCE_ORDER if reports[source] is not None]
    if not present:
        raise ValueError(EMPTY_LEDGER_MESSAGE)

    currencies = {
        source: _source_currency(source, reports[source] or {})
        for source in present
    }
    known_currencies = {value for value in currencies.values() if _known_currency(value)}
    mixed = len(known_currencies) > 1

    base_currency = "unknown"
    if "settlement" in currencies and _known_currency(currencies["settlement"]):
        base_currency = currencies["settlement"]
    else:
        for source in _SOURCE_ORDER:
            value = currencies.get(source, "unknown")
            if _known_currency(value):
                base_currency = value
                break
        if base_currency == "unknown" and present:
            base_currency = currencies[present[0]]

    included = {
        source: (
            not mixed
            or not _known_currency(currencies[source])
            or currencies[source] == base_currency
        )
        for source in present
    }

    missing = [source for source in _SOURCE_ORDER if source not in present]
    missing_labels = [_SOURCE_LABELS[source] for source in missing]
    if len(present) == 3:
        coverage_message = "3/3 份报表已覆盖，三个漏钱来源都已进入总账。"
    else:
        coverage_message = (
            f"只上传了 {len(present)}/3 份报表，还有 {len(missing)} 个洞没看："
            f"{'、'.join(missing_labels)}。"
        )

    warnings: List[str] = []
    missing_names = {
        "settlement": "结算",
        "inventory": "库存龄期",
        "ppc": "搜索词",
    }
    for source in missing:
        warnings.append(
            f"未上传{missing_names[source]}报表，{_SOURCE_LABELS[source]}相关金额按 0 处理。"
        )
    if "settlement" in missing:
        warnings.append("未上传结算报表，「已流失难追回」只包含广告亏本订单部分。")

    if mixed:
        other_currencies = sorted(
            {
                value
                for source, value in currencies.items()
                if _known_currency(value) and value != base_currency
            }
        )
        currency_note = (
            f"多币种：已按 {base_currency} 合计，"
            f"{'、'.join(other_currencies)} 报表未并入，避免汇率臆造。"
        )
        warnings.append(
            f"检测到多币种报表：{'、'.join(other_currencies)} 未并入 {base_currency} 总账。"
        )
    elif _known_currency(base_currency):
        currency_note = (
            f"所有 headline 金额均按 {base_currency} 原币合计，未做任何汇率换算。"
        )
    else:
        currency_note = "未识别到明确币种，headline 金额按报表原始金额直接合计，未做汇率换算。"

    settlement_report = reports["settlement"] or {}
    inventory_report = reports["inventory"] or {}
    ppc_report = reports["ppc"] or {}
    inventory_totals = (
        inventory_report.get("totals")
        if isinstance(inventory_report.get("totals"), dict)
        else {}
    )
    ppc_waste = ppc_report.get("waste") if isinstance(ppc_report.get("waste"), dict) else {}
    ppc_recoverable = (
        ppc_report.get("recoverable")
        if isinstance(ppc_report.get("recoverable"), dict)
        else {}
    )

    settlement_anomaly_recoverable = 0.0
    if included.get("settlement", False):
        for anomaly in settlement_report.get("anomalies") or []:
            if not isinstance(anomaly, dict):
                continue
            if _text(anomaly.get("code")).strip().lower() in _RECOVERABLE_ANOMALY_CODES:
                settlement_anomaly_recoverable += abs(_amount(anomaly.get("amount")))

    recoverable_cash = settlement_anomaly_recoverable
    if included.get("ppc", False):
        recoverable_cash += _amount(ppc_recoverable.get("conservative_total"))

    locked_capital = (
        _amount(inventory_totals.get("locked_cost"))
        if included.get("inventory", False)
        else 0.0
    )

    lost_cash = 0.0
    if included.get("settlement", False):
        lost_cash += _amount(settlement_report.get("refund_total"))
    if included.get("ppc", False):
        lost_cash += _amount(ppc_waste.get("negative_margin_loss"))

    headline = {
        "recoverable_cash": _amount(recoverable_cash),
        "locked_capital": _amount(locked_capital),
        "lost_cash": _amount(lost_cash),
    }

    items: List[Dict[str, Any]] = []
    signals: List[Dict[str, Any]] = []
    if reports["ppc"] is not None:
        items.extend(
            _ppc_items(
                ppc_report,
                currencies["ppc"],
                included["ppc"],
                base_currency,
            )
        )
    if reports["settlement"] is not None:
        settlement_items, settlement_signals = _settlement_items(
            settlement_report,
            currencies["settlement"],
            included["settlement"],
            base_currency,
        )
        items.extend(settlement_items)
        signals.extend(settlement_signals)
    if reports["inventory"] is not None:
        items.extend(
            _inventory_items(
                inventory_report,
                currencies["inventory"],
                included["inventory"],
                base_currency,
            )
        )

    items.sort(
        key=lambda row: (
            _SEVERITY_RANK.get(str(row.get("severity")), 3),
            -_number(row.get("amount")),
            _SOURCE_ORDER.index(str(row.get("source")))
            if str(row.get("source")) in _SOURCE_ORDER
            else 9,
            str(row.get("title")),
        )
    )

    def action_key(row: Dict[str, Any]) -> tuple:
        return (
            _SEVERITY_RANK.get(str(row.get("severity")), 3),
            -_number(row.get("amount")),
            _SOURCE_ORDER.index(str(row.get("source")))
            if str(row.get("source")) in _SOURCE_ORDER
            else 9,
            str(row.get("title")),
        )

    recoverable_candidates = sorted(
        (row for row in items if row.get("bucket") == "recoverable"),
        key=action_key,
    )
    action_candidates = recoverable_candidates[:3]
    if len(action_candidates) < 3:
        locked_candidates = sorted(
            (row for row in items if row.get("bucket") == "locked"),
            key=action_key,
        )
        action_candidates.extend(locked_candidates[: 3 - len(action_candidates)])
    top_actions = [
        {
            "rank": index,
            "source": row["source"],
            "source_label": row["source_label"],
            "title": row["title"],
            "action": row["action"],
            "amount": _amount(row["amount"]),
            "tool_url": row["tool_url"],
            "evidence": row["evidence"],
        }
        for index, row in enumerate(action_candidates[:3], start=1)
    ]

    top_titles = "；".join(
        f"{row['rank']}. {row['title']}（{_money(row['amount'], base_currency)}）"
        for row in top_actions
    )
    verdict = (
        f"今天先做 {len(top_actions)} 件事：{top_titles or '暂无动作'}。"
        f"本周期可追回现金 {_money(headline['recoverable_cash'], base_currency)}；"
        f"另有 {_money(headline['locked_capital'], base_currency)} 被压在库存里"
        "（存量资金占用，不是本期流失）；"
        f"已流失难追回 {_money(headline['lost_cash'], base_currency)}。"
    )
    if len(present) < 3:
        verdict += f" 当前只覆盖 {len(present)}/3 份报表，缺失部分按 0 处理。"

    return {
        "currency": "mixed" if mixed else (_currency(base_currency) or "unknown"),
        "currency_note": currency_note,
        "coverage": {
            "reports": len(present),
            "total": 3,
            "missing": missing_labels,
            "message": coverage_message,
        },
        "headline": headline,
        "metrics": _metrics(
            settlement=reports["settlement"],
            inventory=reports["inventory"],
            ppc=reports["ppc"],
            headline=headline,
            currencies=currencies,
            included=included,
        ),
        "items": items,
        "signals": signals[:6],
        "top_actions": top_actions,
        "verdict": verdict,
        "warnings": list(dict.fromkeys(warnings)),
    }


__all__ = ["EMPTY_LEDGER_MESSAGE", "build_ledger"]
