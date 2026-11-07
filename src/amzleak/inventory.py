# -*- coding: utf-8 -*-
"""Feature 274: 亚马逊库存龄期报表上传审计引擎。

本模块只做标准库解析与确定性纯计算，不依赖 FastAPI、不读环境变量、不碰数据库。
上传文件由 API 层在内存中传入；只有用户主动留邮箱下载清货动作 CSV 时，API 层
才会写入一条不含行级明细的聚合线索。
"""

from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import search_terms as report_parser


AGE_BUCKETS: List[Tuple[str, str, int, int]] = [
    ("age_0_30", "0-30 天", 0, 30),
    ("age_31_60", "31-60 天", 31, 60),
    ("age_61_90", "61-90 天", 61, 90),
    ("age_91_180", "91-180 天", 91, 180),
    ("age_181_270", "181-270 天", 181, 270),
    ("age_271_365", "271-365 天", 271, 365),
    ("age_365_plus", "365 天以上", 366, 99999),
]
AGE_KEYS = tuple(bucket[0] for bucket in AGE_BUCKETS)
AGE_LABELS = {key: label for key, label, _low, _high in AGE_BUCKETS}
_AGE_RANGES = {(low, high): key for key, _label, low, high in AGE_BUCKETS}

_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "sku": ("sku", "seller sku", "卖家sku", "商品sku", "merchant sku"),
    "fnsku": ("fnsku",),
    "asin": ("asin",),
    "title": ("title", "product name", "product-name", "商品名称", "品名"),
    "available": (
        "available",
        "your available inventory",
        "afn fulfillable quantity",
        "afn-fulfillable-quantity",
        "可用库存",
        "可售库存",
        "您的可用库存",
        "afn可售数量",
        "库存数量",
    ),
    "ltsf": (
        "estimated ltsf 365",
        "estimated-ltsf-365",
        "estimated ltsf",
        "estimated-ltsf",
        "ltsf",
        "预计的长期仓储费",
        "预估长期仓储费",
        "长期仓储费",
    ),
    "volume": (
        "item volume",
        "item-volume",
        "cubic feet",
        "cubic-feet",
        "商品体积",
        "每件商品体积",
        "体积立方英尺",
        "商品体积立方英尺",
    ),
    "sell_through": ("sell through", "sell-through", "售出率"),
    "price": ("your price", "your-price", "price", "售价", "商品价格"),
}

_PUBLIC_FIELDS = (
    "sku",
    "fnsku",
    "asin",
    "title",
    "available",
    *AGE_KEYS,
    "ltsf",
    "volume",
    "sell_through",
    "price",
)
_EMPTY_NUMERIC_VALUES = {"", "-", "—", "–", "n/a", "na", "null", "none"}
_MAX_ROWS = 50_000
_MAX_ACTION_ROWS = 2_000

_TIER_RULES: Dict[str, Dict[str, Any]] = {
    "age_0_30": {
        "action": "正常在售",
        "discount_pct": 0,
        "sell_through_pct": None,
    },
    "age_31_60": {
        "action": "正常在售",
        "discount_pct": 0,
        "sell_through_pct": None,
    },
    "age_61_90": {
        "action": "观察，准备促销",
        "discount_pct": 0,
        "sell_through_pct": None,
    },
    "age_91_180": {
        "action": "降价促销",
        "discount_pct": 15,
        "sell_through_pct": 65,
    },
    "age_181_270": {
        "action": "深度清货",
        "discount_pct": 30,
        "sell_through_pct": 60,
    },
    "age_271_365": {
        "action": "清货 + 评估移除",
        "discount_pct": 45,
        "sell_through_pct": 55,
    },
    "age_365_plus": {
        "action": "移除或骨折清货",
        "discount_pct": 60,
        "sell_through_pct": 50,
    },
}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalized_header(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "")
    text = text.lower().replace("\xa0", " ")
    return re.sub(r"[\s_\-./()\[\]（）【】]+", "", text)


_COMPACT_ALIASES: Dict[str, Tuple[str, ...]] = {
    field: tuple(_normalized_header(alias) for alias in aliases)
    for field, aliases in _COLUMN_ALIASES.items()
}


def _age_bucket_from_header(value: Any) -> Optional[str]:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "").lower()
    text = text.replace("\xa0", " ").strip()
    compact = re.sub(r"[\s_\-./()\[\]（）【】]+", "", text)
    if not compact:
        return None
    if "365" in compact and any(
        marker in compact for marker in ("+", "plus", "以上", "超过", "大于")
    ):
        return "age_365_plus"
    if not any(marker in compact for marker in ("age", "库龄", "天", "day")):
        return None

    numbers = [int(number) for number in re.findall(r"\d+", text)]
    if len(numbers) >= 2:
        low, high = numbers[0], numbers[1]
        if high > low:
            return _AGE_RANGES.get((low, high))
    if len(numbers) == 1 and numbers[0] >= 365:
        return "age_365_plus"
    return None


def map_columns(headers: Sequence[Any]) -> Dict[str, Optional[str]]:
    """把亚马逊中英文库存龄期报表表头映射到 canonical 字段名。"""
    source_headers = [_cell_text(header).replace("\ufeff", "").strip() for header in (headers or [])]
    compact_headers = [_normalized_header(header) for header in source_headers]
    mapped: Dict[str, Optional[str]] = {field: None for field in _PUBLIC_FIELDS}

    for field in _PUBLIC_FIELDS:
        if field in AGE_KEYS:
            continue
        aliases = _COMPACT_ALIASES[field]
        for index, compact in enumerate(compact_headers):
            if compact and compact in aliases:
                mapped[field] = source_headers[index]
                break

    for index, compact in enumerate(compact_headers):
        bucket = _age_bucket_from_header(source_headers[index])
        if bucket and not mapped.get(bucket):
            mapped[bucket] = source_headers[index]
    return mapped


def _clean_number(value: Any) -> Tuple[float, bool, bool]:
    """返回 (数值, 是否有效, 是否为空占位符)。"""
    if value is None:
        return 0.0, True, True
    if isinstance(value, bool):
        return float(value), True, False
    if isinstance(value, (int, float)):
        return float(value), True, False

    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "").strip()
    if text.lower() in _EMPTY_NUMERIC_VALUES:
        return 0.0, True, True
    text = re.sub(r"(?i)us\s*\$", "", text)
    for symbol in ("$", "€", "¥", "￥", ",", "%", " "):
        text = text.replace(symbol, "")
    text = text.replace("\xa0", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if not text:
        return 0.0, True, True
    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0, False, False
    return (-number if negative else number), True, False


def _as_int(value: Any) -> int:
    number, valid, _empty = _clean_number(value)
    return max(0, int(round(number))) if valid else 0


def _as_float(value: Any) -> float:
    number, valid, _empty = _clean_number(value)
    return max(0.0, float(number)) if valid else 0.0


def _optional_float(value: Any) -> Optional[float]:
    number, valid, empty = _clean_number(value)
    if not valid or empty:
        return None
    return max(0.0, float(number))


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def _row_value(row: Any, header: Optional[str]) -> Any:
    if not header:
        return ""
    if isinstance(row, dict):
        return row.get(header, "")
    return ""


def _matrix_row_value(row: Sequence[Any], headers: Sequence[str], header: Optional[str]) -> Any:
    if not header:
        return ""
    try:
        return row[list(headers).index(header)]
    except (ValueError, IndexError):
        return ""


def _row_is_blank(row: Any, headers: Sequence[str], mapped: Dict[str, Optional[str]]) -> bool:
    if isinstance(row, dict):
        values = list(row.values())
    elif isinstance(row, (list, tuple)):
        values = list(row)
    else:
        values = [row]
    return not values or all(not _cell_text(value).strip() for value in values)


def normalize_rows(headers, rows) -> dict:
    """把报表行清洗为 SKU 记录，并把库龄差额确定性归入 0-30 天。"""
    header_list = [_cell_text(header).replace("\ufeff", "").strip() for header in (headers or [])]
    mapped = map_columns(header_list)
    has_age_columns = any(mapped.get(key) for key in AGE_KEYS)
    has_available = bool(mapped.get("available"))
    source_rows = [row for row in (rows or []) if not _row_is_blank(row, header_list, mapped)]
    warnings: List[str] = []

    truncated = len(source_rows) > _MAX_ROWS
    if truncated:
        source_rows = source_rows[:_MAX_ROWS]
        warnings.append(f"报表超过 {_MAX_ROWS} 行，仅分析前 {_MAX_ROWS} 行。")
    if not has_age_columns:
        warnings.append("报表没有可识别的库龄列，已将可用库存全部归入 0-30 天。")

    records: List[Dict[str, Any]] = []
    skipped = 0
    filled_rows = 0
    exceeded_rows = 0
    invalid_columns: List[str] = []

    for row in source_rows:
        if isinstance(row, dict):
            def value_for(field: str) -> Any:
                return _row_value(row, mapped.get(field))
        elif isinstance(row, (list, tuple)):
            def value_for(field: str) -> Any:
                return _matrix_row_value(row, header_list, mapped.get(field))
        else:
            skipped += 1
            continue

        sku = _clean_text(value_for("sku"))
        asin = _clean_text(value_for("asin"))
        title = _clean_text(value_for("title"))
        if not any((sku, asin, title)):
            skipped += 1
            continue

        buckets: Dict[str, int] = {}
        for key in AGE_KEYS:
            original = mapped.get(key)
            if not original:
                buckets[key] = 0
                continue
            number, valid, _empty = _clean_number(value_for(key))
            if not valid and original not in invalid_columns:
                invalid_columns.append(original)
            buckets[key] = max(0, int(round(number))) if valid else 0

        available_header = mapped.get("available")
        available_raw = value_for("available")
        available_number, available_valid, available_empty = _clean_number(available_raw)
        if available_header and not available_valid and available_header not in invalid_columns:
            invalid_columns.append(available_header)
        available = max(0, int(round(available_number))) if available_valid else 0
        bucket_total = sum(buckets.values())

        if not has_age_columns:
            buckets["age_0_30"] = available
            bucket_total = available
        elif available_empty or not has_available:
            available = bucket_total
        elif bucket_total < available:
            buckets["age_0_30"] += available - bucket_total
            filled_rows += 1
        elif bucket_total > available:
            available = bucket_total
            exceeded_rows += 1

        records.append(
            {
                "sku": sku,
                "asin": asin,
                "title": title,
                "available": available,
                "buckets": buckets,
                "ltsf": round(_as_float(value_for("ltsf")), 2),
                "volume": round(_as_float(value_for("volume")), 4),
                "price": _optional_float(value_for("price")),
                "sell_through": _optional_float(value_for("sell_through")),
            }
        )

    if filled_rows:
        warnings.append(f"有 {filled_rows} 行库龄合计小于可用库存，差额已补入 0-30 天。")
    if exceeded_rows:
        warnings.append(f"有 {exceeded_rows} 行库龄合计大于可用库存，已按库龄合计作为可用库存。")
    if invalid_columns:
        warnings.append("列「" + "、".join(invalid_columns[:5]) + "」含无法识别的数值，已按 0 处理。")
    if skipped:
        warnings.append(f"有 {skipped} 行缺少 SKU / ASIN / 商品名称，已跳过。")

    return {
        "records": records,
        "rows_total": len(source_rows),
        "skipped": skipped,
        "warnings": warnings,
        "columns": mapped,
        "has_age_columns": has_age_columns,
        "has_ltsf_column": bool(mapped.get("ltsf")),
    }


def _infer_currency(rows: Iterable[Any]) -> str:
    values: List[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            values.extend(_cell_text(value) for value in row.values())
        elif isinstance(row, (list, tuple)):
            values.extend(_cell_text(value) for value in row)
    joined = "\n".join(values)
    if "€" in joined:
        return "EUR"
    if "£" in joined:
        return "GBP"
    if "¥" in joined or "￥" in joined:
        return "CNY"
    if "$" in joined:
        return "USD"
    return "unknown"


_INVENTORY_ERROR = (
    "这不是亚马逊库存龄期报表：未找到库存数量/库龄列。"
    "请在 Seller Central 下载「库存龄期」报表后重试。"
)


def parse_inventory_report(data: bytes, filename: str) -> dict:
    """解析 CSV / TSV / TXT / XLSX 库存龄期报表，并完成列映射与归一化。"""
    try:
        parsed = report_parser.parse_report_bytes(data, filename)
    except ValueError as exc:
        raise ValueError(_INVENTORY_ERROR) from exc

    headers = parsed.get("headers") or []
    mapped = map_columns(headers)
    if not mapped.get("available") and all(not mapped.get(key) for key in AGE_KEYS):
        raise ValueError(_INVENTORY_ERROR)

    normalized = normalize_rows(headers, parsed.get("rows") or [])
    return {
        **normalized,
        "headers": headers,
        "encoding": parsed.get("encoding", ""),
        "delimiter": parsed.get("delimiter", ""),
        "currency": _infer_currency(parsed.get("rows") or []),
        "ltsf_source": "report" if normalized["has_ltsf_column"] else "missing",
    }


def _tier_metrics(quantity: int, tier_key: str, unit_cost: float, expected_price: float,
                  referral_fee_pct: float, removal_fee: float) -> Dict[str, Any]:
    rule = _TIER_RULES[tier_key]
    quantity = max(0, int(quantity or 0))
    cost = quantity * unit_cost
    discount_pct = int(rule["discount_pct"])
    sell_through_pct = rule["sell_through_pct"]
    recoverable = 0.0
    removal_units = 0
    if tier_key in {"age_91_180", "age_181_270", "age_271_365", "age_365_plus"} and quantity:
        sell_units = int(math.ceil(quantity * float(sell_through_pct) / 100.0))
        net_price = expected_price * (1.0 - discount_pct / 100.0) * (1.0 - referral_fee_pct / 100.0)
        recoverable = max(0.0, sell_units * net_price)
        if tier_key == "age_365_plus":
            removal_units = max(0, quantity - sell_units)
    return {
        "cost": round(cost, 2),
        "recoverable_cash": round(recoverable, 2),
        "removal_units": removal_units,
        "removal_cost": round(removal_units * removal_fee, 2),
    }


def _action_csv(rows: Sequence[Dict[str, Any]], currency: str) -> Tuple[str, bool]:
    ordered = sorted(
        rows,
        key=lambda item: (
            -float(item.get("locked_cost") or 0.0),
            -int(item.get("units") or 0),
            str(item.get("asin") or ""),
            str(item.get("sku") or ""),
        ),
    )
    truncated = len(ordered) > _MAX_ACTION_ROWS
    ordered = ordered[:_MAX_ACTION_ROWS]

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "ASIN",
            "SKU",
            "商品名称",
            "库龄段",
            "库存件数",
            "压死资金(成本口径)",
            "建议动作",
            "建议折扣",
            "预计可回收现金",
            "预计移除件数",
            "备注",
        )
    )
    for item in ordered:
        writer.writerow(
            (
                item.get("asin", ""),
                item.get("sku", ""),
                item.get("title", ""),
                item.get("bucket_label", ""),
                int(item.get("units") or 0),
                f"{float(item.get('locked_cost') or 0.0):.2f}",
                item.get("action", ""),
                f"{int(item.get('discount_pct') or 0)}%",
                f"{float(item.get('recoverable_cash') or 0.0):.2f}",
                int(item.get("removal_units") or 0),
                item.get("note", "") + (f"；币种 {currency}" if currency and currency != "unknown" else ""),
            )
        )
    return output.getvalue(), truncated


def audit_inventory(
    records,
    unit_cost: float = 8.0,
    expected_price: float = 29.9,
    referral_fee_pct: float = 15.0,
    removal_fee: float = 0.97,
    currency: str = "unknown",
) -> dict:
    """按固定清货假设计算压死资金、可回收现金与动作文件。"""
    records = list(records or [])
    unit_cost = max(0.0, float(unit_cost or 0.0))
    expected_price = max(0.0, float(expected_price or 0.0))
    referral_fee_pct = min(100.0, max(0.0, float(referral_fee_pct or 0.0)))
    removal_fee = max(0.0, float(removal_fee or 0.0))
    currency = str(currency or "unknown")

    tier_quantities = {key: 0 for key in AGE_KEYS}
    sku_count = 0
    total_units = 0
    ltsf_total = 0.0
    ltsf_present = False
    total_recoverable = 0.0
    total_removal_units = 0
    total_removal_cost = 0.0
    action_csv_rows: List[Dict[str, Any]] = []

    for record in records:
        sku_count += 1
        available = max(0, int(record.get("available") or 0))
        total_units += available
        buckets = record.get("buckets") or {}
        ltsf = max(0.0, float(record.get("ltsf") or 0.0))
        ltsf_total += ltsf
        if ltsf > 0:
            ltsf_present = True
        for key in AGE_KEYS:
            tier_quantities[key] += max(0, int(buckets.get(key) or 0))

        for key in ("age_61_90", "age_91_180", "age_181_270", "age_271_365", "age_365_plus"):
            quantity = max(0, int(buckets.get(key) or 0))
            if quantity <= 0:
                continue
            metrics = _tier_metrics(
                quantity,
                key,
                unit_cost,
                expected_price,
                referral_fee_pct,
                removal_fee,
            )
            locked_cost = round(quantity * unit_cost, 2) if key in {
                "age_91_180", "age_181_270", "age_271_365", "age_365_plus"
            } else 0.0
            total_recoverable += metrics["recoverable_cash"]
            total_removal_units += metrics["removal_units"]
            total_removal_cost += metrics["removal_cost"]
            sell_through_pct = _TIER_RULES[key]["sell_through_pct"]
            expected_sold = (
                int(math.ceil(quantity * float(sell_through_pct) / 100.0))
                if sell_through_pct is not None
                else 0
            )
            note = (
                f"清货期售出率 {sell_through_pct}%；预计清货 {expected_sold}/{quantity} 件"
                if sell_through_pct is not None
                else "先观察，不立即降价"
            )
            action_csv_rows.append(
                {
                    "asin": record.get("asin", ""),
                    "sku": record.get("sku", ""),
                    "title": record.get("title", ""),
                    "bucket": key,
                    "bucket_label": AGE_LABELS[key],
                    "units": quantity,
                    "locked_cost": locked_cost,
                    "action": _TIER_RULES[key]["action"],
                    "discount_pct": _TIER_RULES[key]["discount_pct"],
                    "recoverable_cash": metrics["recoverable_cash"],
                    "removal_units": metrics["removal_units"],
                    "note": note,
                }
            )

    inventory_cost = round(total_units * unit_cost, 2)
    locked_keys = ("age_91_180", "age_181_270", "age_271_365", "age_365_plus")
    aged_units = sum(tier_quantities[key] for key in locked_keys)
    locked_cost = round(aged_units * unit_cost, 2)
    locked_ratio = min(1.0, locked_cost / inventory_cost) if inventory_cost > 0 else 0.0
    ltsf_total = round(ltsf_total, 2)
    ltsf_penalty = min(25.0, ltsf_total / max(1.0, inventory_cost) * 100.0)
    score = int(round(max(0.0, 100.0 - locked_ratio * 140.0 - ltsf_penalty)))
    if locked_ratio <= 0.15:
        grade = "A"
    elif locked_ratio <= 0.30:
        grade = "B"
    elif locked_ratio <= 0.50:
        grade = "C"
    else:
        grade = "D"

    tiers: List[Dict[str, Any]] = []
    action_groups: Dict[str, Dict[str, Any]] = {}
    for key, label, _low, _high in AGE_BUCKETS:
        quantity = tier_quantities[key]
        metrics = _tier_metrics(
            quantity,
            key,
            unit_cost,
            expected_price,
            referral_fee_pct,
            removal_fee,
        )
        tier = {
            "key": key,
            "label": label,
            "units": quantity,
            "cost": metrics["cost"],
            "share": round(metrics["cost"] / inventory_cost, 4) if inventory_cost > 0 else 0.0,
            "action": _TIER_RULES[key]["action"],
            "discount_pct": _TIER_RULES[key]["discount_pct"],
            "sell_through_pct": _TIER_RULES[key]["sell_through_pct"],
            "recoverable_cash": metrics["recoverable_cash"],
        }
        tiers.append(tier)
        action = str(tier["action"])
        group = action_groups.setdefault(
            action,
            {"action": action, "sku_count": 0, "_skus": set(), "units": 0, "cost": 0.0, "recoverable_cash": 0.0},
        )
        group["units"] += quantity
        group["cost"] = round(float(group["cost"]) + float(tier["cost"]), 2)
        group["recoverable_cash"] = round(
            float(group["recoverable_cash"]) + float(tier["recoverable_cash"]), 2
        )

    for record in records:
        buckets = record.get("buckets") or {}
        for key, label, _low, _high in AGE_BUCKETS:
            if int(buckets.get(key) or 0) <= 0:
                continue
            group = action_groups.setdefault(
                _TIER_RULES[key]["action"],
                {
                    "action": _TIER_RULES[key]["action"],
                    "sku_count": 0,
                    "_skus": set(),
                    "units": 0,
                    "cost": 0.0,
                    "recoverable_cash": 0.0,
                },
            )
            group["_skus"].add(record.get("sku") or record.get("asin") or record.get("title") or "")

    actions = []
    for group in action_groups.values():
        if int(group.get("units") or 0) <= 0:
            continue
        group["sku_count"] = len(group.pop("_skus"))
        actions.append(group)

    top_items: List[Dict[str, Any]] = []
    for record in records:
        buckets = record.get("buckets") or {}
        for key, label, _low, _high in reversed(AGE_BUCKETS):
            quantity = max(0, int(buckets.get(key) or 0))
            if quantity <= 0 or key in {"age_0_30", "age_31_60"}:
                continue
            metrics = _tier_metrics(
                quantity,
                key,
                unit_cost,
                expected_price,
                referral_fee_pct,
                removal_fee,
            )
            top_items.append(
                {
                    "sku": record.get("sku", ""),
                    "asin": record.get("asin", ""),
                    "title": record.get("title", ""),
                    "bucket": key,
                    "bucket_label": label,
                    "units": quantity,
                    "locked_cost": round(quantity * unit_cost, 2),
                    "action": _TIER_RULES[key]["action"],
                    "discount_pct": _TIER_RULES[key]["discount_pct"],
                    "recoverable_cash": metrics["recoverable_cash"],
                    "removal_units": metrics["removal_units"],
                }
            )
            break
    top_items.sort(key=lambda item: (-float(item["locked_cost"]), -int(item["units"]), str(item["sku"])))

    action_csv, action_csv_truncated = _action_csv(action_csv_rows, currency)
    warnings: List[str] = []
    if action_csv_truncated:
        warnings.append(f"清货动作超过 {_MAX_ACTION_ROWS} 行，CSV 仅保留压死资金最高的前 {_MAX_ACTION_ROWS} 行。")

    return {
        "params": {
            "unit_cost": round(unit_cost, 2),
            "expected_price": round(expected_price, 2),
            "referral_fee_pct": round(referral_fee_pct, 2),
            "removal_fee": round(removal_fee, 2),
            "currency": currency,
        },
        "totals": {
            "sku_count": sku_count,
            "units": total_units,
            "inventory_cost": inventory_cost,
            "aged_units": aged_units,
            "locked_cost": locked_cost,
            "locked_ratio": round(locked_ratio, 4),
            "ltsf_total": ltsf_total,
            "ltsf_source": "report" if ltsf_present else "missing",
            "recoverable_cash": round(total_recoverable, 2),
            "removal_units": total_removal_units,
            "removal_cost": round(total_removal_cost, 2),
            "score": score,
            "grade": grade,
        },
        "tiers": tiers,
        "actions": actions,
        "top_items": top_items[:15],
        "action_csv": action_csv,
        "warnings": warnings,
    }


_SAMPLE_HEADERS = (
    "卖家 SKU",
    "FNSKU",
    "ASIN",
    "商品名称",
    "状况",
    "可用库存",
    "库龄 0-30 天",
    "库龄 31-60 天",
    "库龄 61-90 天",
    "库龄 91-180 天",
    "库龄 181-270 天",
    "库龄 271-365 天",
    "库龄 365 天以上",
    "预计的长期仓储费",
    "商品体积（立方英尺）",
    "售出率",
    "售价",
)

_SAMPLE_PRODUCTS: Tuple[Tuple[str, str], ...] = (
    ("3C", "无线蓝牙耳机"),
    ("3C", "65W 氮化镓快充充电器"),
    ("3C", "磁吸防摔手机壳"),
    ("3C", "便携蓝牙音箱"),
    ("3C", "USB-C 编织数据线"),
    ("3C", "智能手表表带"),
    ("3C", "静音无线鼠标"),
    ("3C", "铝合金笔记本支架"),
    ("3C", "车载磁吸手机支架"),
    ("3C", "10000mAh 移动电源"),
    ("3C", "桌面 USB 麦克风"),
    ("3C", "手机摄影补光灯"),
    ("3C", "蓝牙音频接收器"),
    ("3C", "多口充电底座"),
    ("家居", "香薰蜡烛礼盒"),
    ("家居", "厨房透明收纳盒"),
    ("家居", "硅胶烘焙垫"),
    ("家居", "浴室免打孔置物架"),
    ("家居", "记忆棉护腰靠垫"),
    ("家居", "北欧风装饰挂画"),
    ("家居", "不锈钢保温杯"),
    ("家居", "折叠收纳凳"),
    ("家居", "护眼桌面台灯"),
    ("家居", "地毯防滑贴"),
    ("家居", "可折叠脏衣篮"),
    ("家居", "厨房沥水篮"),
    ("家居", "真空压缩收纳袋"),
    ("家居", "陶瓷调味罐套装"),
    ("服饰", "女士运动背心"),
    ("服饰", "男士速干 T 恤"),
    ("服饰", "儿童纯棉中筒袜"),
    ("服饰", "高腰瑜伽裤"),
    ("服饰", "防晒渔夫帽"),
    ("服饰", "针织开衫外套"),
    ("服饰", "轻量运动短裤"),
    ("服饰", "家居防滑拖鞋"),
    ("服饰", "旅行衣物收纳袋"),
    ("服饰", "帆布托特包"),
    ("服饰", "运动吸汗发带"),
    ("服饰", "可调节棒球帽"),
)


def sample_report_csv() -> str:
    """生成 40 行合成库存龄期报表（官方中文表头，非真实卖家数据）。"""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    output.write("\ufeff")
    writer.writerow(_SAMPLE_HEADERS)

    for index, (category, title) in enumerate(_SAMPLE_PRODUCTS):
        if index == 0:
            buckets = (0, 0, 0, 0, 0, 20, 180)
            ltsf = 431.20
            volume = 0.085
            sell_through = 1.2
            price = 39.90
        elif index % 11 == 1:
            buckets = (0, 0, 0, 0, 45, 25, 0)
            ltsf = 18.60
            volume = 0.12
            sell_through = 3.1
            price = 49.90
        elif index % 7 == 2:
            buckets = (0, 0, 0, 80, 20, 0, 0)
            ltsf = 0.0
            volume = 0.06
            sell_through = 8.5
            price = 29.90
        elif index % 5 == 3:
            buckets = (0, 0, 60, 10, 0, 0, 0)
            ltsf = 0.0
            volume = 0.045
            sell_through = 14.0
            price = 19.90
        elif index % 3 == 0:
            buckets = (24, 16, 0, 45, 0, 0, 0)
            ltsf = 0.0
            volume = 0.055
            sell_through = 22.5
            price = 24.90
        else:
            buckets = (30 + (index % 9) * 4, 10 + index % 7, 0, 0, 0, 0, 0)
            ltsf = 0.0
            volume = 0.04 + (index % 5) * 0.015
            sell_through = 35.0 + (index % 6) * 4.5
            price = (12.90, 16.90, 21.90, 27.90, 34.90)[index % 5]

        available = sum(buckets)
        prefix = {"3C": "3C", "家居": "HM", "服饰": "FS"}[category]
        writer.writerow(
            (
                f"IA-{prefix}-{index + 1:03d}",
                f"X00IA{index + 1:05d}",
                f"B0IA274{index + 1:03d}",
                title,
                "新品",
                available,
                *buckets,
                f"{ltsf:.2f}",
                f"{volume:.3f}",
                f"{sell_through:.1f}%",
                f"{price:.2f}",
            )
        )
    return output.getvalue()


__all__ = [
    "AGE_BUCKETS",
    "AGE_KEYS",
    "audit_inventory",
    "map_columns",
    "normalize_rows",
    "parse_inventory_report",
    "sample_report_csv",
]
