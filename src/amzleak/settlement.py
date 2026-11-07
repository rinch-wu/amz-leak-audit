# -*- coding: utf-8 -*-
"""Feature 275: 亚马逊结算报表真实净利审计引擎。

本模块只依赖标准库与仓库既有的 openpyxl，不依赖 FastAPI、环境变量或数据库。
上传文件只在调用方内存中解析；所有计算均为确定性纯函数。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import search_terms as report_parser


MAX_ROWS = 200_000
_MAX_ROWS = MAX_ROWS
MAX_ACTION_ROWS = 2_000

CANONICAL_FIELDS: Tuple[str, ...] = (
    "settlement_id",
    "settlement_start_date",
    "settlement_end_date",
    "deposit_date",
    "total_amount",
    "currency",
    "transaction_type",
    "order_id",
    "amount_type",
    "amount_description",
    "amount",
    "posted_date",
    "posted_date_time",
    "sku",
    "quantity_purchased",
    "fulfillment_id",
    "marketplace_name",
)

_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "settlement_id": ("settlement-id", "settlement id", "settlement_id", "结算编号", "结算单号"),
    "settlement_start_date": (
        "settlement-start-date",
        "settlement start date",
        "settlement_start_date",
        "结算开始日期",
    ),
    "settlement_end_date": (
        "settlement-end-date",
        "settlement end date",
        "settlement_end_date",
        "结算结束日期",
    ),
    "deposit_date": ("deposit-date", "deposit date", "deposit_date", "到账日期", "打款日期"),
    "total_amount": ("total-amount", "total amount", "total_amount", "结算总额"),
    "currency": ("currency", "币种", "货币"),
    "transaction_type": ("transaction-type", "transaction type", "transaction_type", "交易类型"),
    "order_id": ("order-id", "order id", "order_id", "订单号", "亚马逊订单号"),
    "amount_type": ("amount-type", "amount type", "amount_type", "金额类型"),
    "amount_description": (
        "amount-description",
        "amount description",
        "amount_description",
        "金额说明",
    ),
    "amount": ("amount", "金额", "交易金额"),
    "posted_date": ("posted-date", "posted date", "posted_date", "过账日期"),
    "posted_date_time": (
        "posted-date-time",
        "posted date time",
        "posted_date_time",
        "posteddatetime",
        "过账时间",
    ),
    "sku": ("sku", "seller sku", "seller-sku", "卖家sku", "商品sku"),
    "quantity_purchased": (
        "quantity-purchased",
        "quantity purchased",
        "quantity_purchased",
        "购买数量",
        "数量",
    ),
    "fulfillment_id": ("fulfillment-id", "fulfillment id", "fulfillment_id", "配送方式"),
    "marketplace_name": ("marketplace-name", "marketplace name", "marketplace_name", "站点"),
}

_PLACEHOLDER_VALUES = {"", "-", "—", "–", "n/a", "na", "null", "none", "无"}

BUCKET_ORDER: Tuple[str, ...] = (
    "sales",
    "shipping_income",
    "promo",
    "commission",
    "fba_fees",
    "refunds",
    "ads",
    "service",
    "tax",
    "other",
)

BUCKET_LABELS: Dict[str, str] = {
    "sales": "商品销售额",
    "shipping_income": "运费收入",
    "promo": "促销折扣",
    "commission": "平台佣金",
    "fba_fees": "FBA 履约与仓储费",
    "refunds": "退款与退货侵蚀",
    "ads": "广告费",
    "service": "订阅与服务费",
    "tax": "税费",
    "other": "其他",
}

BUCKET_RULES: Dict[str, str] = {
    "sales": "amount-type = ItemPrice 且 desc = Principal",
    "shipping_income": "amount-type = Shipment 且 desc ∈ {Shipping, ShippingHB}",
    "promo": "amount-type ∈ {ItemPromotion, ShipmentPromotion}",
    "commission": "ItemFees 且 desc 含 Commission；或 amount-type ∈ {Order, OrderRetrocharge}",
    "fba_fees": "ItemFees 且 desc 含 FBA；或 amount-type = FBAInventoryFee",
    "refunds": "amount-type 以 Refund 开头（含 RefundRetrocharge / RefundTax）",
    "ads": "amount-type = CostofAdvertising",
    "service": "amount-type = ServiceFee",
    "tax": "amount-type 含 Tax",
    "other": "以上都不匹配的剩余交易",
}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: Any) -> str:
    text = _clean_text(value).lower().replace("\xa0", " ")
    return re.sub(r"[\s_\-./()\[\]（）【】]+", "", text)


_COMPACT_ALIASES: Dict[str, Tuple[str, ...]] = {
    field: tuple(_compact(alias) for alias in aliases)
    for field, aliases in _COLUMN_ALIASES.items()
}


def map_columns(headers: Sequence[Any]) -> Dict[str, Optional[str]]:
    """把亚马逊结算报表表头映射为 canonical 字段名。"""
    normalized = [_compact(header) for header in headers]
    mapped: Dict[str, Optional[str]] = {}
    used: set = set()
    for field in CANONICAL_FIELDS:
        matched: Optional[str] = None
        for index, value in enumerate(normalized):
            if not value or index in used:
                continue
            if value in _COMPACT_ALIASES[field]:
                matched = _clean_text(headers[index])
                used.add(index)
                break
        mapped[field] = matched
    return mapped


def clean_amount(value: Any) -> float:
    """清洗 ``$1,234.56``、``(45.00)``、空值与 ``-`` 等金额格式。"""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)

    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "").strip()
    if text.lower() in _PLACEHOLDER_VALUES:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = re.sub(r"(?i)us\s*\$", "", text)
    for symbol in ("$", "€", "£", "¥", "￥", ",", "\xa0", " ", "−"):
        text = text.replace(symbol, "-" if symbol == "−" else "")
    if text.endswith("-") and not text.startswith("-"):
        negative = True
        text = text[:-1]
    if not text:
        return 0.0
    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0
    return -number if negative else number


_clean_amount = clean_amount


def classify_row(amount_type: Any, description: Any) -> str:
    """按归一化后的 (amount-type, amount-description) 返回分桶 key。"""
    kind = _compact(amount_type)
    desc = _compact(description)

    if kind == "itemprice" and desc == "principal":
        return "sales"
    if kind == "shipment" and desc in {"shipping", "shippinghb"}:
        return "shipping_income"
    if kind in {"itempromotion", "shipmentpromotion"}:
        return "promo"
    if kind.startswith("refund"):
        return "refunds"
    if kind == "itemfees" and "commission" in desc:
        return "commission"
    if kind in {"order", "orderretrocharge"}:
        return "commission"
    if kind == "itemfees" and "fba" in desc:
        return "fba_fees"
    if kind == "fbainventoryfee":
        return "fba_fees"
    if kind == "costofadvertising":
        return "ads"
    if kind == "servicefee":
        return "service"
    if "tax" in kind:
        return "tax"
    return "other"


def _parse_date(value: Any) -> Optional[dt.date]:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text).date()
    except (TypeError, ValueError):
        pass
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _header_index(matrix: Sequence[Sequence[Any]]) -> int:
    best_index = -1
    best_score = 0
    for index, row in enumerate(matrix[:80]):
        if report_parser._row_is_blank(row):
            continue
        mapped = map_columns(row)
        score = sum(1 for value in mapped.values() if value)
        if score > best_score:
            best_score = score
            best_index = index
    if best_index < 0 or best_score < 2:
        raise ValueError(
            "未识别到亚马逊结算报表表头。请从 Seller Central → 报表 → 付款 → "
            "日期范围报表 → 下载（制表符分隔 .txt）后重新上传。"
        )
    return best_index


def _matrix_from_bytes(data: bytes, filename: str) -> List[List[Any]]:
    safe_name = _clean_text(filename).lower()
    if safe_name.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except Exception as exc:  # pragma: no cover - openpyxl 为项目既有依赖
            raise ValueError("无法读取 XLSX：当前环境缺少 openpyxl。") from exc
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = workbook.active
            matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
            workbook.close()
        except Exception as exc:
            raise ValueError("无法读取 XLSX 文件，请确认文件未损坏。") from exc
        return matrix

    text, _encoding = report_parser._decode_report_bytes(data)
    delimiter = report_parser._detect_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        return [list(row) for row in reader]
    except csv.Error as exc:
        raise ValueError("无法解析报表文本，请确认文件是 CSV / TSV / TXT 格式。") from exc


def parse_settlement_report(
    data: bytes,
    filename: str = "settlement.txt",
    max_rows: Optional[int] = None,
) -> dict:
    """解析结算报表并返回规范化交易记录。"""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError(
            "文件为空，请上传 Seller Central → 报表 → 付款 → 日期范围报表下载的结算报表。"
        )
    matrix = _matrix_from_bytes(bytes(data), filename)
    if not matrix or all(report_parser._row_is_blank(row) for row in matrix):
        raise ValueError("报表内容为空，请重新下载结算报表后再试。")

    header_index = _header_index(matrix)
    headers = report_parser._clean_headers(matrix[header_index])
    mapped = map_columns(headers)
    missing = [field for field in ("amount", "amount_type") if not mapped.get(field)]
    if missing:
        actual = "、".join(header for header in headers if _clean_text(header))[:360]
        raise ValueError(
            "结算报表缺少必要列：amount / amount-type。请确认文件来自 "
            "Seller Central → 报表 → 付款 → 日期范围报表 → 下载（制表符分隔 .txt）。"
            f"当前识别到的列名：{actual or '（无表头）'}"
        )

    try:
        if max_rows is None:
            limit = min(int(MAX_ROWS), int(_MAX_ROWS))
        else:
            limit = max(1, int(max_rows))
    except (TypeError, ValueError):
        limit = min(int(MAX_ROWS), int(_MAX_ROWS))

    warnings: List[str] = []
    invalid_amount_column = False
    records: List[Dict[str, Any]] = []
    seen_rows = 0
    for raw_row in matrix[header_index + 1 :]:
        if report_parser._row_is_blank(raw_row):
            continue
        seen_rows += 1
        if len(records) >= limit:
            continue

        raw: Dict[str, Any] = {}
        for index, header in enumerate(headers):
            if header:
                raw[header] = raw_row[index] if index < len(raw_row) else ""

        def source(canonical: str) -> Any:
            original = mapped.get(canonical)
            return raw.get(original, "") if original else ""

        amount_raw = source("amount")
        amount = round(clean_amount(amount_raw), 2)
        if _clean_text(amount_raw) and _clean_text(amount_raw).lower() not in _PLACEHOLDER_VALUES:
            if amount == 0.0 and not re.search(r"0(?:\.0+)?", _clean_text(amount_raw)):
                invalid_amount_column = True

        posted = _clean_text(source("posted_date_time")) or _clean_text(source("posted_date"))
        record = {
            "settlement_id": _clean_text(source("settlement_id")),
            "settlement_start_date": _clean_text(source("settlement_start_date")),
            "settlement_end_date": _clean_text(source("settlement_end_date")),
            "deposit_date": _clean_text(source("deposit_date")),
            "total_amount": round(clean_amount(source("total_amount")), 2),
            "currency": _clean_text(source("currency")).upper() or "unknown",
            "transaction_type": _clean_text(source("transaction_type")),
            "order_id": _clean_text(source("order_id")),
            "amount_type": _clean_text(source("amount_type")),
            "amount_description": _clean_text(source("amount_description")),
            "amount": amount,
            "posted_date": posted,
            "sku": _clean_text(source("sku")),
            "quantity_purchased": _clean_text(source("quantity_purchased")),
            "fulfillment_id": _clean_text(source("fulfillment_id")),
            "marketplace_name": _clean_text(source("marketplace_name")),
        }
        records.append(record)

    truncated = seen_rows > limit
    if truncated:
        warnings.append(f"报表共 {seen_rows} 行，仅分析前 {limit} 行。")
    if invalid_amount_column:
        warnings.append("部分行 amount 无法识别，已按 0 处理。")

    return {
        "records": records,
        "rows_total": len(records),
        "rows_seen": seen_rows,
        "skipped": 0,
        "headers": headers,
        "columns": mapped,
        "warnings": warnings,
    }


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float, digits: int = 2) -> float:
    return round(float(value or 0.0), digits)


def _pct(part: float, total: float) -> float:
    return _round((float(part or 0.0) / float(total or 0.0)) * 100.0, 2) if total else 0.0


def _currency_summary(records: Sequence[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    counts: Counter = Counter()
    first_seen: List[str] = []
    totals: Dict[str, Dict[str, float]] = {}
    for record in records:
        currency = _clean_text(record.get("currency")).upper() or "unknown"
        if currency not in totals:
            totals[currency] = {"rows": 0.0, "net": 0.0, "gross_sales": 0.0}
            first_seen.append(currency)
        counts[currency] += 1
        totals[currency]["rows"] += 1
        totals[currency]["net"] += _as_float(record.get("amount"))
        if classify_row(record.get("amount_type"), record.get("amount_description")) == "sales":
            totals[currency]["gross_sales"] += _as_float(record.get("amount"))

    if not first_seen:
        return "unknown", [], []
    primary = max(first_seen, key=lambda item: (counts[item], -first_seen.index(item)))
    summary = [
        {
            "currency": currency,
            "rows": int(totals[currency]["rows"]),
            "net_deposit": _round(totals[currency]["net"]),
            "gross_sales": _round(totals[currency]["gross_sales"]),
        }
        for currency in first_seen
    ]
    warnings: List[str] = []
    if len(first_seen) > 1:
        warnings.append(
            f"检测到 {len(first_seen)} 种币种（{'、'.join(first_seen)}），"
            f"核心指标仅统计行数最多的 {primary}，未做任何汇率换算。"
        )
    return primary, summary, warnings


def _period_days(records: Sequence[Dict[str, Any]]) -> Tuple[int, str, str]:
    posted_dates = [
        parsed
        for parsed in (_parse_date(record.get("posted_date")) for record in records)
        if parsed is not None
    ]
    if posted_dates:
        start, end = min(posted_dates), max(posted_dates)
        return max(1, (end - start).days), start.isoformat(), end.isoformat()

    starts = [
        parsed
        for parsed in (_parse_date(record.get("settlement_start_date")) for record in records)
        if parsed is not None
    ]
    ends = [
        parsed
        for parsed in (_parse_date(record.get("settlement_end_date")) for record in records)
        if parsed is not None
    ]
    if starts and ends:
        start, end = min(starts), max(ends)
        return max(1, (end - start).days), start.isoformat(), end.isoformat()
    return 30, "", ""


def _build_reconciliation(records: Sequence[Dict[str, Any]]) -> dict:
    computed: Dict[str, float] = {}
    stated: Dict[str, float] = {}
    order: List[str] = []
    for record in records:
        settlement_id = _clean_text(record.get("settlement_id"))
        if not settlement_id:
            continue
        if settlement_id not in computed:
            computed[settlement_id] = 0.0
            stated[settlement_id] = 0.0
            order.append(settlement_id)
        computed[settlement_id] += _as_float(record.get("amount"))
        total_amount = _as_float(record.get("total_amount"))
        if total_amount:
            stated[settlement_id] = total_amount

    mismatches: List[Dict[str, Any]] = []
    matched = 0
    for settlement_id in order:
        stated_value = _round(stated.get(settlement_id, 0.0))
        computed_value = _round(computed.get(settlement_id, 0.0))
        if not stated_value:
            continue
        delta = _round(computed_value - stated_value)
        if abs(delta) > 0.02:
            mismatches.append(
                {
                    "id": settlement_id,
                    "computed": computed_value,
                    "stated": stated_value,
                    "delta": delta,
                }
            )
        else:
            matched += 1
    return {
        "settlements": len(order),
        "matched": matched,
        "mismatched": mismatches,
    }


def _build_sku_rows(records: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    aggregated: Dict[str, Dict[str, Any]] = {}
    unassigned = {
        "sku": "(未标注 SKU)",
        "sales": 0.0,
        "commission": 0.0,
        "fba": 0.0,
        "refund": 0.0,
        "net": 0.0,
        "quantity": 0,
        "rows": 0,
    }
    for record in records:
        sku = _clean_text(record.get("sku"))
        target = aggregated.setdefault(
            sku,
            {
                "sku": sku,
                "sales": 0.0,
                "commission": 0.0,
                "fba": 0.0,
                "refund": 0.0,
                "net": 0.0,
                "quantity": 0,
                "rows": 0,
            },
        )
        amount = _as_float(record.get("amount"))
        bucket = classify_row(record.get("amount_type"), record.get("amount_description"))
        if not sku:
            target = unassigned
        target["rows"] += 1
        target["net"] += amount
        target["quantity"] += int(clean_amount(record.get("quantity_purchased")))
        if bucket == "sales":
            target["sales"] += amount
        elif bucket == "commission":
            target["commission"] += amount
        elif bucket == "fba_fees":
            target["fba"] += amount
        elif bucket == "refunds":
            target["refund"] += amount

    rows: List[Dict[str, Any]] = []
    for sku, values in aggregated.items():
        if not sku:
            continue
        sales = _round(values["sales"])
        net = _round(values["net"])
        rows.append(
            {
                "sku": sku,
                "sales": sales,
                "commission": _round(values["commission"]),
                "fba": _round(values["fba"]),
                "refund": _round(values["refund"]),
                "net": net,
                "margin_pct": _pct(net, sales),
                "quantity": int(values["quantity"]),
                "rows": int(values["rows"]),
            }
        )
    rows.sort(key=lambda item: (item["net"], item["sku"]))
    unassigned_summary = {
        "sku": unassigned["sku"],
        "sales": _round(unassigned["sales"]),
        "net": _round(unassigned["net"]),
        "rows": int(unassigned["rows"]),
        "included_in_ranking": False,
    }
    return rows, unassigned_summary


def _detect_anomalies(
    records: Sequence[Dict[str, Any]],
    bucket_totals: Dict[str, float],
    gross_sales: float,
    refund_total: float,
    ad_rate: float,
    reconciliation: dict,
    sku_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    anomalies: List[Dict[str, Any]] = []

    duplicate_groups: Dict[Tuple[str, str, str, float, str], List[Dict[str, Any]]] = {}
    for record in records:
        amount = _round(record.get("amount"))
        if amount >= 0:
            continue
        key = (
            _clean_text(record.get("order_id")),
            _compact(record.get("amount_type")),
            _compact(record.get("amount_description")),
            amount,
            _clean_text(record.get("posted_date")),
        )
        duplicate_groups.setdefault(key, []).append(record)
    duplicated = [group for group in duplicate_groups.values() if len(group) >= 2]
    if duplicated:
        duplicate_extra = sum(abs(_as_float(group[0].get("amount"))) * (len(group) - 1) for group in duplicated)
        anomalies.append(
            {
                "severity": "high",
                "code": "duplicate_fee",
                "title": "疑似重复扣费",
                "detail": f"发现 {len(duplicated)} 组同订单、同类型、同金额、同过账日的重复扣费，多扣约 {duplicate_extra:.2f}。",
                "amount": _round(duplicate_extra),
            }
        )

    commission_outliers = [
        row
        for row in sku_rows
        if row["sales"] >= 200 and row["sales"] > 0 and abs(row["commission"]) / row["sales"] > 0.18
    ]
    if commission_outliers:
        outliers = sorted(
            commission_outliers,
            key=lambda item: abs(item["commission"]) / max(item["sales"], 0.01),
            reverse=True,
        )[:5]
        detail = "；".join(
            f"{item['sku']} 佣金率 {abs(item['commission']) / item['sales'] * 100:.1f}%" for item in outliers
        )
        anomalies.append(
            {
                "severity": "mid",
                "code": "commission_outlier",
                "title": "SKU 佣金率偏高",
                "detail": f"以下 SKU 佣金 / 销售额超过 18%：{detail}。",
                "amount": _round(sum(abs(item["commission"]) for item in outliers)),
            }
        )

    negative_skus = [row for row in sku_rows if row["sales"] > 0 and row["net"] < 0]
    if negative_skus:
        worst = sorted(negative_skus, key=lambda item: item["net"])[:5]
        detail = "；".join(f"{item['sku']} 净额 {item['net']:.2f}" for item in worst)
        anomalies.append(
            {
                "severity": "high",
                "code": "negative_sku",
                "title": "卖得越多亏得越多",
                "detail": f"{len(negative_skus)} 个有销售的 SKU 净额为负，最差：{detail}。",
                "amount": _round(abs(sum(item["net"] for item in worst))),
            }
        )

    refund_rate = _pct(refund_total, gross_sales)
    if refund_rate > 6:
        anomalies.append(
            {
                "severity": "high",
                "code": "high_refund_rate",
                "title": "退款侵蚀偏高",
                "detail": f"退款率 {refund_rate:.2f}%，超过 6% 警戒线。",
                "amount": _round(refund_total),
            }
        )

    if ad_rate > 15:
        anomalies.append(
            {
                "severity": "mid",
                "code": "high_ad_rate",
                "title": "广告占比偏高",
                "detail": f"广告费占销售额 {ad_rate:.2f}%，超过 15% 警戒线。",
                "amount": _round(abs(bucket_totals.get("ads", 0.0))),
            }
        )

    if reconciliation.get("mismatched"):
        mismatch_total = sum(abs(item.get("delta", 0.0)) for item in reconciliation["mismatched"])
        anomalies.append(
            {
                "severity": "high",
                "code": "unreconciled",
                "title": "结算批次对账不平",
                "detail": f"{len(reconciliation['mismatched'])} 个 settlement-id 的交易合计与 total-amount 不一致。",
                "amount": _round(mismatch_total),
            }
        )

    refund_orders = {
        _clean_text(record.get("order_id"))
        for record in records
        if _compact(record.get("amount_type")).startswith("refund")
        and _clean_text(record.get("order_id"))
    }
    fee_on_refund = 0.0
    fee_rows = 0
    for record in records:
        order_id = _clean_text(record.get("order_id"))
        if not order_id or order_id not in refund_orders:
            continue
        token = _compact(record.get("amount_type")) + "|" + _compact(record.get("amount_description"))
        if "refundcommission" in token or "shippingchargeback" in token:
            amount = _as_float(record.get("amount"))
            if amount < 0:
                fee_on_refund += abs(amount)
                fee_rows += 1
    if fee_rows:
        anomalies.append(
            {
                "severity": "mid",
                "code": "fee_on_refund",
                "title": "退款订单仍被收费",
                "detail": f"{fee_rows} 条退款订单记录包含退款佣金或运费追回费用。",
                "amount": _round(fee_on_refund),
            }
        )

    return anomalies


def _fee_total(bucket_totals: Dict[str, float], other_fees: float) -> float:
    return _round(
        -(
            bucket_totals.get("commission", 0.0)
            + bucket_totals.get("fba_fees", 0.0)
            + bucket_totals.get("ads", 0.0)
            + bucket_totals.get("service", 0.0)
            + other_fees
        )
    )


def _grade_base(net_margin_pct: float) -> str:
    if net_margin_pct >= 20:
        return "A"
    elif net_margin_pct >= 12:
        return "B"
    elif net_margin_pct >= 5:
        return "C"
    return "D"


def _grade(net_margin_pct: float, refund_rate: float, ad_rate: float) -> str:
    grade = _grade_base(net_margin_pct)
    if (refund_rate > 8 or ad_rate > 20) and grade in {"A", "B"}:
        grade = "C"
    return grade


def _grade_reason(
    net_margin_pct: float,
    grade: str,
    grade_base: str,
    refund_rate: float,
    ad_rate: float,
) -> str:
    margin = f"{_round(net_margin_pct):.2f}"
    refund = f"{_round(refund_rate):.2f}"
    ad = f"{_round(ad_rate):.2f}"
    if grade == grade_base:
        return (
            f"净利率 {margin}% 对应 {grade_base} 级，退款率 {refund}%、广告占比 {ad}% "
            "均在阈值内（退款率 ≤ 8%、广告占比 ≤ 20%）。"
        )

    reasons = []
    if refund_rate > 8:
        reasons.append(f"退款率 {refund}% 超过 8% 阈值")
    if ad_rate > 20:
        reasons.append(f"广告占比 {ad}% 超过 20% 阈值")
    reason_text = "、".join(reasons)
    return (
        f"净利率 {margin}% 本应对应 {grade_base} 级，但{reason_text}，"
        f"按风险下调为 {grade} 级。"
    )


def audit_settlement(
    records: Sequence[Dict[str, Any]],
    expected_margin_pct: float = 25.0,
    currency: Optional[str] = None,
) -> dict:
    """计算真实净利、费用结构、对账、异常和 SKU 净利排名。"""
    all_records = [record for record in (records or []) if isinstance(record, dict)]
    if not all_records:
        raise ValueError("没有可分析的结算交易记录，请确认报表不是空文件。")

    primary_currency, currency_summary, warnings = _currency_summary(all_records)
    selected_currency = _clean_text(currency).upper() if currency else primary_currency
    selected = [
        record
        for record in all_records
        if (_clean_text(record.get("currency")).upper() or "unknown") == selected_currency
    ]
    if not selected:
        selected = all_records
        selected_currency = primary_currency

    bucket_totals: Dict[str, float] = {key: 0.0 for key in BUCKET_ORDER}
    bucket_counts: Dict[str, int] = {key: 0 for key in BUCKET_ORDER}
    unclassified_counts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in selected:
        bucket = classify_row(record.get("amount_type"), record.get("amount_description"))
        amount = _as_float(record.get("amount"))
        bucket_totals[bucket] += amount
        bucket_counts[bucket] += 1
        if bucket == "other":
            key = (
                _clean_text(record.get("amount_type")),
                _clean_text(record.get("amount_description")),
            )
            item = unclassified_counts.setdefault(
                key,
                {
                    "amount_type": key[0],
                    "description": key[1],
                    "count": 0,
                    "amount": 0.0,
                },
            )
            item["count"] += 1
            item["amount"] += amount

    bucket_totals = {key: _round(value) for key, value in bucket_totals.items()}
    gross_sales = _round(bucket_totals["sales"])
    shipping_income = _round(bucket_totals["shipping_income"])
    promo = _round(bucket_totals["promo"])
    collected_gross = _round(gross_sales + shipping_income + promo)
    other_fees = _round(
        sum(
            _as_float(record.get("amount"))
            for record in selected
            if classify_row(record.get("amount_type"), record.get("amount_description")) == "other"
            and _as_float(record.get("amount")) < 0
        )
    )
    fee_total = _fee_total(bucket_totals, other_fees)
    refund_total = _round(-bucket_totals["refunds"])
    net_deposit = _round(sum(_as_float(record.get("amount")) for record in selected))

    take_home_per_100 = _pct(net_deposit, gross_sales)
    net_margin_pct = take_home_per_100
    commission_rate = _pct(abs(bucket_totals["commission"]), gross_sales)
    fba_rate = _pct(abs(bucket_totals["fba_fees"]), gross_sales)
    ad_rate = _pct(abs(bucket_totals["ads"]), gross_sales)
    refund_rate = _pct(refund_total, gross_sales)
    service_rate = _pct(abs(bucket_totals["service"]), gross_sales)
    expected = _round(expected_margin_pct)
    margin_gap_pct = _round(net_margin_pct - expected)
    gap_dollars_period = _round((net_margin_pct - expected) / 100.0 * gross_sales)
    period_days, period_start, period_end = _period_days(selected)
    gap_dollars_annual = _round(gap_dollars_period / max(period_days, 1) * 365.0)
    grade_base = _grade_base(net_margin_pct)
    grade = _grade(net_margin_pct, refund_rate, ad_rate)

    fee_buckets: List[Dict[str, Any]] = []
    for key in BUCKET_ORDER:
        amount = _round(bucket_totals[key])
        fee_buckets.append(
            {
                "key": key,
                "label": BUCKET_LABELS[key],
                "amount": amount,
                "count": int(bucket_counts[key]),
                "share_pct": _pct(abs(amount), gross_sales),
                "rule": BUCKET_RULES[key],
            }
        )

    fee_driver_amounts = {
        "commission": abs(bucket_totals["commission"]),
        "fba_fees": abs(bucket_totals["fba_fees"]),
        "ads": abs(bucket_totals["ads"]),
        "service": abs(bucket_totals["service"]),
        "refunds": refund_total,
    }
    top_fee_drivers = sorted(
        (
            {
                "key": key,
                "label": BUCKET_LABELS[key],
                "amount": _round(amount),
                "share_pct": _pct(amount, gross_sales),
            }
            for key, amount in fee_driver_amounts.items()
        ),
        key=lambda item: item["amount"],
        reverse=True,
    )[:5]

    reconciliation = _build_reconciliation(selected)
    reconciliation_ok = not reconciliation["mismatched"]
    sku_rows, unassigned_sku = _build_sku_rows(selected)
    losing_skus = [row for row in sku_rows if row["sales"] > 0 and row["net"] < 0][:10]
    winning_skus = sorted(
        (row for row in sku_rows if row["net"] > 0),
        key=lambda item: (item["net"], item["sku"]),
        reverse=True,
    )[:10]
    anomalies = _detect_anomalies(
        selected,
        bucket_totals,
        gross_sales,
        refund_total,
        ad_rate,
        reconciliation,
        sku_rows,
    )

    unclassified = sorted(
        (
            {
                "amount_type": item["amount_type"],
                "description": item["description"],
                "amount_description": item["description"],
                "count": int(item["count"]),
                "amount": _round(item["amount"]),
            }
            for item in unclassified_counts.values()
        ),
        key=lambda item: (abs(item["amount"]), item["count"]),
        reverse=True,
    )[:10]

    if not reported_total_column(selected):
        warnings.append("报表未提供有效的 total-amount 列，无法逐批次对账。")
    if any(_clean_text(record.get("amount_type")).lower() == "tax" for record in selected):
        warnings.append("税费按报表原始符号计入净入账；代扣与代缴方向请以结算报表为准。")

    report: Dict[str, Any] = {
        "currency": selected_currency,
        "primary_currency": primary_currency,
        "currencies": currency_summary,
        "rows_total": len(selected),
        "all_rows_total": len(all_records),
        "gross_sales": gross_sales,
        "collected_gross": collected_gross,
        "shipping_income": shipping_income,
        "promo": promo,
        "other_fees": other_fees,
        "fee_total": fee_total,
        "refund_total": refund_total,
        "net_deposit": net_deposit,
        "take_home_per_100": take_home_per_100,
        "net_margin_pct": net_margin_pct,
        "commission_rate": commission_rate,
        "fba_rate": fba_rate,
        "ad_rate": ad_rate,
        "refund_rate": refund_rate,
        "service_rate": service_rate,
        "expected_margin_pct": expected,
        "margin_gap_pct": margin_gap_pct,
        "gap_dollars_period": gap_dollars_period,
        "gap_dollars_annual": gap_dollars_annual,
        "period_days": period_days,
        "period_start": period_start,
        "period_end": period_end,
        "grade": grade,
        "grade_base": grade_base,
        "grade_reason": _grade_reason(net_margin_pct, grade, grade_base, refund_rate, ad_rate),
        "buckets": fee_buckets,
        "bucket_totals": bucket_totals,
        "top_fee_drivers": top_fee_drivers,
        "reconciliation": reconciliation,
        "reconciliation_ok": reconciliation_ok,
        "anomalies": anomalies,
        "sku_rows": sku_rows,
        "sku_rows_losses": [row for row in sku_rows if row["sales"] > 0 and row["net"] < 0][:10],
        "sku_rows_winners": sorted(
            (row for row in sku_rows if row["net"] > 0),
            key=lambda item: (item["net"], item["sku"]),
            reverse=True,
        )[:10],
        "losing_skus": losing_skus,
        "winning_skus": winning_skus,
        "unassigned_sku": unassigned_sku,
        "unclassified": unclassified,
        "warnings": warnings,
    }
    report["action_csv"] = build_action_csv(report)
    return report


def reported_total_column(records: Sequence[Dict[str, Any]]) -> bool:
    return any(_as_float(record.get("total_amount")) for record in records)


def _fee_action(key: str, share_pct: float) -> str:
    actions = {
        "sales": "核对销售额与订单退款，确认是否有未入账批次",
        "shipping_income": "核对运费收入是否覆盖真实尾程成本",
        "promo": "复核 Coupon / Deal / 促销叠加，砍掉无增量折扣",
        "commission": "抽查佣金率，异常 SKU 提交费用复核或开 Case",
        "fba_fees": "复核尺寸分段、低库存费与仓储费，调整补货节奏",
        "refunds": "定位高退款 SKU，检查质量问题与 listing 预期",
        "ads": "按 ACOS 关停亏损词，把预算挪给高净利 SKU",
        "service": "核对订阅与服务费是否仍在使用",
        "tax": "核对税费代扣代缴方向与税务申报口径",
        "other": "逐项核对未归类交易，避免费用漏看",
    }
    return f"{BUCKET_LABELS.get(key, key)}占销售额 {share_pct:.2f}% → {actions.get(key, '逐项核对')}"


def build_action_csv(report: Dict[str, Any]) -> str:
    """生成带 UTF-8 BOM 的失血明细动作 CSV。"""
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(("row_type", "section", "key", "label", "amount", "share_pct", "note"))

    gross_sales = _as_float(report.get("gross_sales"))

    def add(row_type: str, section: str, key: str, label: str, amount: float, share: float, note: str) -> None:
        writer.writerow(
            (
                row_type,
                section,
                key,
                label,
                f"{_as_float(amount):.2f}",
                f"{_as_float(share):.2f}",
                note,
            )
        )

    summary_rows = (
        ("gross_sales", "商品销售额", report.get("gross_sales"), "客户购买商品的账面销售额"),
        ("net_deposit", "净入账", report.get("net_deposit"), "结算报表全部 amount 的合计"),
        ("take_home_per_100", "每 $100 销售额到手", report.get("take_home_per_100"), "净入账 / 销售额 × 100"),
        ("fee_total", "费用合计", report.get("fee_total"), "佣金 + FBA + 广告 + 服务费 + 其他费用"),
        ("refund_total", "退款合计", report.get("refund_total"), "退款本金与退款相关费用绝对值"),
        (
            "margin_gap_pct",
            "净利率缺口",
            report.get("margin_gap_pct"),
            "真实净利率 - 预期净利率，正数=高于预期",
        ),
        (
            "gap_dollars_period",
            "本周期与预期差额",
            report.get("gap_dollars_period"),
            "真实净利率减预期净利率后按销售额折算，正数=比预期多赚，负数=比预期少赚",
        ),
        (
            "gap_dollars_annual",
            "年化与预期差额",
            report.get("gap_dollars_annual"),
            "按报表天数线性外推，正数=比预期多赚，负数=比预期少赚",
        ),
    )
    for key, label, amount, note in summary_rows:
        add("summary", "kpi", key, label, _as_float(amount), 0.0, note)

    for bucket in report.get("buckets") or []:
        key = str(bucket.get("key") or "")
        share = _as_float(bucket.get("share_pct"))
        add(
            "fee",
            "fees",
            key,
            str(bucket.get("label") or key),
            _as_float(bucket.get("amount")),
            share,
            _fee_action(key, share),
        )

    for anomaly in report.get("anomalies") or []:
        add(
            "anomaly",
            "anomalies",
            str(anomaly.get("code") or ""),
            str(anomaly.get("title") or ""),
            _as_float(anomaly.get("amount")),
            _pct(_as_float(anomaly.get("amount")), gross_sales),
            f"[{anomaly.get('severity') or 'mid'}] {anomaly.get('detail') or ''}",
        )

    losing_skus = list(report.get("losing_skus") or [])
    if not losing_skus:
        losing_skus = [row for row in (report.get("sku_rows") or []) if _as_float(row.get("net")) < 0]
    for row in losing_skus[:MAX_ACTION_ROWS]:
        net = _as_float(row.get("net"))
        add(
            "sku",
            "losing_skus",
            str(row.get("sku") or ""),
            str(row.get("sku") or ""),
            net,
            _pct(net, gross_sales),
            (
                f"销售额 {_as_float(row.get('sales')):.2f}，退款 {_as_float(row.get('refund')):.2f}，"
                f"净额 {net:.2f} → 先查退款与广告，再决定提价、停售或换物流"
            ),
        )
    return output.getvalue()


_SAMPLE_HEADERS: Tuple[str, ...] = (
    "settlement-id",
    "settlement-start-date",
    "settlement-end-date",
    "deposit-date",
    "total-amount",
    "currency",
    "transaction-type",
    "order-id",
    "amount-type",
    "amount-description",
    "amount",
    "posted-date",
    "posted-date-time",
    "sku",
    "quantity-purchased",
    "fulfillment-id",
    "marketplace-name",
)


def _sample_row(
    settlement_id: str,
    start: str,
    end: str,
    deposit: str,
    transaction_type: str,
    order_id: str,
    amount_type: str,
    description: str,
    amount: float,
    posted: str,
    sku: str = "",
    quantity: int = 0,
    fulfillment: str = "FBA",
) -> Dict[str, Any]:
    return {
        "settlement_id": settlement_id,
        "start": start,
        "end": end,
        "deposit": deposit,
        "transaction_type": transaction_type,
        "order_id": order_id,
        "amount_type": amount_type,
        "description": description,
        "amount": round(amount, 2),
        "posted": posted,
        "sku": sku,
        "quantity": quantity,
        "fulfillment": fulfillment,
    }


def sample_report_csv() -> str:
    """生成 3 个结算批次、4 个 SKU 的合成结算报表 TSV。"""
    skus = ("PHONE-CASE-BLK", "LED-STRIP-5M", "Yoga-Mat-Pro", "CABLE-USB-C-2M")
    batches: List[Dict[str, Any]] = []

    specs = (
        (
            "S-2025-001",
            "2025-01-01",
            "2025-01-15",
            "2025-01-17",
            "2025-01-04",
            (1200.0, 900.0, 700.0, 500.0),
            (25.0, 20.0, 18.0, 12.0),
            (-60.0, -45.0, -35.0, -25.0),
            (-180.0, -135.0, -105.0, -75.0),
            (-25.0, -22.0, -18.0, -14.0),
            (-120.0, -100.0, -220.0, -80.0),
            True,
        ),
        (
            "S-2025-002",
            "2025-01-16",
            "2025-01-31",
            "2025-02-03",
            "2025-01-20",
            (1000.0, 800.0, 500.0, 600.0),
            (21.0, 18.0, 14.0, 16.0),
            (-50.0, -40.0, -25.0, -30.0),
            (-150.0, -120.0, -75.0, -90.0),
            (-22.0, -18.0, -15.0, -16.0),
            (-110.0, -95.0, -140.0, -70.0),
            False,
        ),
        (
            "S-2025-003",
            "2025-02-01",
            "2025-02-10",
            "2025-02-12",
            "2025-02-05",
            (600.0, 400.0, 300.0, 250.0),
            (13.0, 10.0, 8.0, 7.0),
            (-30.0, -20.0, -15.0, -12.0),
            (-90.0, -60.0, -45.0, -37.5),
            (-16.0, -12.0, -10.0, -9.0),
            (-65.0, -55.0, -80.0, -40.0),
            False,
        ),
    )

    for batch_index, (
        settlement_id,
        start,
        end,
        deposit,
        posted,
        prices,
        shipping,
        promos,
        commissions,
        fba_fees,
        ads,
        with_yoga_refund,
    ) in enumerate(specs):
        rows: List[Dict[str, Any]] = []
        for index, sku in enumerate(skus):
            order_id = f"{batch_index + 1}12-{1000000 + batch_index * 1000 + index * 37}"
            rows.extend(
                [
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Order",
                        order_id,
                        "ItemPrice",
                        "Principal",
                        prices[index],
                        posted,
                        sku,
                        1 + (index + batch_index) % 4,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Order",
                        order_id,
                        "ItemFees",
                        "Commission",
                        commissions[index],
                        posted,
                        sku,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Order",
                        order_id,
                        "ItemFees",
                        "FBAPerUnitFulfillmentFee",
                        fba_fees[index],
                        posted,
                        sku,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Order",
                        order_id,
                        "Shipment",
                        "Shipping",
                        shipping[index],
                        posted,
                        sku,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Order",
                        order_id,
                        "ItemPromotion",
                        "Principal",
                        promos[index],
                        posted,
                        sku,
                    ),
                ]
            )
        rows.append(
            _sample_row(
                settlement_id,
                start,
                end,
                deposit,
                "Cost of Advertising",
                "",
                "CostofAdvertising",
                "Cost of Advertising",
                sum(ads),
                posted,
                "Yoga-Mat-Pro",
            )
        )

        if batch_index == 1:
            duplicate_order = "212-1001037"
            rows.append(
                _sample_row(
                    settlement_id,
                    start,
                    end,
                    deposit,
                    "Order",
                    duplicate_order,
                    "ItemFees",
                    "Commission",
                    -120.0,
                    posted,
                    "LED-STRIP-5M",
                )
            )

        if with_yoga_refund:
            yoga_order = f"{batch_index + 1}12-{1000000 + batch_index * 1000 + 2 * 37}"
            rows.extend(
                [
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Refund",
                        yoga_order,
                        "Refund",
                        "Principal",
                        -700.0,
                        posted,
                        "Yoga-Mat-Pro",
                        -1,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Refund",
                        yoga_order,
                        "Refund",
                        "RefundCommission",
                        -105.0,
                        posted,
                        "Yoga-Mat-Pro",
                    ),
                ]
            )
        elif batch_index == 1:
            cable_order = f"{batch_index + 1}12-{1000000 + batch_index * 1000 + 3 * 37}"
            rows.extend(
                [
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Refund",
                        cable_order,
                        "Refund",
                        "Principal",
                        -600.0,
                        posted,
                        "CABLE-USB-C-2M",
                        -1,
                    ),
                    _sample_row(
                        settlement_id,
                        start,
                        end,
                        deposit,
                        "Refund",
                        cable_order,
                        "Refund",
                        "RefundCommission",
                        -90.0,
                        posted,
                        "CABLE-USB-C-2M",
                    ),
                ]
            )

        rows.extend(
            [
                _sample_row(
                    settlement_id,
                    start,
                    end,
                    deposit,
                    "Service Fee",
                    "",
                    "ServiceFee",
                    "Subscription",
                    -39.99,
                    posted,
                ),
                _sample_row(
                    settlement_id,
                    start,
                    end,
                    deposit,
                    "FBA Inventory Fee",
                    "",
                    "FBAInventoryFee",
                    "FBA storage fee",
                    -15.0 - batch_index,
                    posted,
                ),
                _sample_row(
                    settlement_id,
                    start,
                    end,
                    deposit,
                    "Order",
                    "",
                    "Tax",
                    "MarketplaceFacilitatorTax",
                    -20.0 - batch_index * 5,
                    posted,
                ),
            ]
        )
        if batch_index == 2:
            rows.append(
                _sample_row(
                    settlement_id,
                    start,
                    end,
                    deposit,
                    "Adjustment",
                    "RETRO-003",
                    "OrderRetrocharge",
                    "Commission retrocharge",
                    -18.0,
                    posted,
                    "CABLE-USB-C-2M",
                )
            )
        batches.append({"id": settlement_id, "start": start, "end": end, "deposit": deposit, "rows": rows})

    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(_SAMPLE_HEADERS)
    for batch in batches:
        total_amount = round(sum(_as_float(row["amount"]) for row in batch["rows"]), 2)
        for row in batch["rows"]:
            writer.writerow(
                (
                    batch["id"],
                    batch["start"],
                    batch["end"],
                    batch["deposit"],
                    f"{total_amount:.2f}",
                    "USD",
                    row["transaction_type"],
                    row["order_id"],
                    row["amount_type"],
                    row["description"],
                    f"{_as_float(row['amount']):.2f}",
                    row["posted"],
                    f"{row['posted']}T09:15:00+00:00",
                    row["sku"],
                    row["quantity"],
                    row["fulfillment"],
                    "Amazon.com",
                )
            )
    return output.getvalue()
