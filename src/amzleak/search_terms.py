# -*- coding: utf-8 -*-
"""Feature 272: 亚马逊广告搜索词报表上传审计引擎。

业务口径（页面与测试共用，所有金额/比率统一保留两位小数）：
- 零转化空耗：订单数为 0 且花费大于 0 的搜索词花费合计。
- ACOS 倒挂亏损：订单数大于 0 时，max(0, 花费 - 销售额 * 保本 ACOS)。
- 保本 ACOS：默认等于用户填写的毛利率。
- 可立即回收：零转化空耗 * 90%（保留 10% 给品牌防守词）。
- 保守可回收：零转化空耗 * 90% + ACOS 倒挂亏损 * 70%。

本模块只做标准库解析与纯计算，不依赖 FastAPI，不读环境变量，不碰数据库。
文件内容由调用方在内存中传入；只有用户主动留邮箱下载批量文件时，API 层才会
写入一条不含报表原文的聚合线索。
"""

from __future__ import annotations

import codecs
import csv
import io
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


CANONICAL_FIELDS: Tuple[str, ...] = (
    "campaign",
    "ad_group",
    "targeting",
    "match_type",
    "search_term",
    "impressions",
    "clicks",
    "spend",
    "sales",
    "orders",
)

_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "campaign": (
        "campaign name",
        "campaign",
        "广告活动名称",
        "广告活动",
        "推广计划",
    ),
    "ad_group": ("ad group name", "ad group", "广告组名称", "广告组"),
    "targeting": ("targeting", "keyword", "投放", "投放词", "关键词"),
    "match_type": ("match type", "匹配类型"),
    "search_term": (
        "customer search term",
        "search term",
        "客户搜索词",
        "买家搜索词",
        "搜索词",
        "query",
    ),
    "impressions": ("impressions", "展示量", "曝光量", "展现量"),
    "clicks": ("clicks", "点击量", "点击数"),
    "spend": ("spend", "cost", "花费", "广告花费", "总成本", "支出"),
    "sales": (
        "7 day total sales",
        "total sales",
        "sales",
        "7天总销售额",
        "销售额",
    ),
    "orders": (
        "7 day total orders",
        "total orders",
        "orders",
        "7天总订单数",
        "订单数",
        "订单量",
    ),
}

_NUMERIC_FIELDS = ("impressions", "clicks", "spend", "sales", "orders")
_MAX_ROWS = 50_000
_MAX_WARNING_COLUMNS = 5
_EMPTY_NUMERIC_VALUES = {"", "-", "—", "–", "n/a", "na", "null", "none"}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_column_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value))
    text = text.replace("\ufeff", "").replace("\xa0", " ").strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact_column_name(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_column_name(value))


_COMPACT_ALIASES: Dict[str, Tuple[str, ...]] = {
    canonical: tuple(_compact_column_name(alias) for alias in aliases)
    for canonical, aliases in _COLUMN_ALIASES.items()
}


def map_columns(headers: list[str]) -> dict:
    """把亚马逊中英文报表表头映射到 canonical 字段名。

    大小写、前后空格、下划线、连字符与全角括号均不敏感；未命中返回 ``None``。
    """
    normalized_headers = [_compact_column_name(header) for header in headers]
    mapped: Dict[str, Optional[str]] = {}
    for canonical in CANONICAL_FIELDS:
        matched: Optional[str] = None
        aliases = _COMPACT_ALIASES[canonical]
        for index, normalized in enumerate(normalized_headers):
            if not normalized:
                continue
            if normalized in aliases:
                matched = _cell_text(headers[index]).replace("\ufeff", "").strip()
                break
        mapped[canonical] = matched
    return mapped


def _mapped_score(headers: Sequence[Any]) -> int:
    mapped = map_columns([_cell_text(header) for header in headers])
    return sum(1 for value in mapped.values() if value)


def _inventory_mapped_score(headers: Sequence[Any]) -> int:
    """库存龄期报表复用同一套文件解码/分隔符/表头定位时的兜底分值。"""
    markers = (
        "sku",
        "fnsku",
        "asin",
        "available",
        "fulfillable",
        "库存",
        "库龄",
        "inv age",
        "inv-age",
        "product name",
        "商品名称",
        "ltsf",
        "长期仓储费",
    )
    score = 0
    for header in headers:
        compact = _compact_column_name(header)
        if compact and any(marker.replace(" ", "") in compact for marker in markers):
            score += 1
    return score


def _detect_delimiter(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()][:20]
    tabs = sum(line.count("\t") for line in lines)
    commas = sum(line.count(",") for line in lines)
    semicolons = sum(line.count(";") for line in lines)
    if semicolons > max(tabs, commas) * 1.25 and semicolons > 0:
        return ";"
    if tabs > commas:
        return "\t"
    return ","


def _decode_report_bytes(data: bytes) -> Tuple[str, str]:
    """按既定顺序回退解码；UTF-16 仅在确有 BOM/零字节特征时尝试。"""
    candidates: List[str] = ["utf-8-sig", "utf-8"] if data.startswith(codecs.BOM_UTF8) else ["utf-8"]
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or b"\x00" in data[:4096]:
        candidates.append("utf-16")
    candidates.extend(["gb18030", "latin-1"])
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    # latin-1 理论上不会失败，保留兜底以防非 bytes 兼容对象被误传。
    return data.decode("latin-1", errors="replace"), "latin-1"


def _clean_headers(row: Sequence[Any]) -> List[str]:
    headers = [_cell_text(cell).replace("\ufeff", "").strip() for cell in row]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def _row_is_blank(row: Sequence[Any]) -> bool:
    return all(not _cell_text(cell).strip() for cell in row)


def _find_header_index(rows: Sequence[Sequence[Any]]) -> int:
    best_index = -1
    best_score = 0
    inventory_index = -1
    inventory_score = 0
    for index, row in enumerate(rows):
        score = _mapped_score(row)
        if score > best_score:
            best_score = score
            best_index = index
        stock_score = _inventory_mapped_score(row)
        if stock_score > inventory_score:
            inventory_score = stock_score
            inventory_index = index
    if best_score < 3 and inventory_score >= 3:
        return inventory_index
    if best_index < 0 or best_score < 3:
        raise ValueError(
            "未识别到搜索词报表表头…请确认文件来自亚马逊广告后台的搜索词报表，"
            "并保留 Campaign / Customer Search Term / Spend 等原始列名。"
        )
    return best_index


def _rows_from_matrix(matrix: Sequence[Sequence[Any]], encoding: str, delimiter: str) -> dict:
    if not matrix:
        raise ValueError("未识别到搜索词报表表头…报表内容为空。")
    header_index = _find_header_index(matrix)
    headers = _clean_headers(matrix[header_index])
    if not headers:
        raise ValueError("未识别到搜索词报表表头…表头行为空。")

    rows: List[Dict[str, str]] = []
    for raw_row in matrix[header_index + 1 :]:
        if _row_is_blank(raw_row):
            continue
        record: Dict[str, str] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = _cell_text(raw_row[index]) if index < len(raw_row) else ""
        if record:
            rows.append(record)
    return {
        "headers": headers,
        "rows": rows,
        "encoding": encoding,
        "delimiter": delimiter,
        "header_row_index": header_index,
    }


def _parse_xlsx(data: bytes) -> dict:
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
    return _rows_from_matrix(matrix, encoding="xlsx", delimiter="xlsx")


def parse_report_bytes(data: bytes, filename: str) -> dict:
    """解析 CSV / TSV / TXT / XLSX 搜索词报表，自动定位前置标题后的真实表头。"""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("文件为空，请上传亚马逊广告后台导出的搜索词报表。")
    raw = bytes(data)
    safe_name = str(filename or "").strip()
    if safe_name.lower().endswith(".xlsx"):
        return _parse_xlsx(raw)

    text, encoding = _decode_report_bytes(raw)
    delimiter = _detect_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        matrix = [list(row) for row in reader]
    except csv.Error as exc:
        raise ValueError("无法解析报表文本，请确认文件是 CSV / TSV / TXT 格式。") from exc
    return _rows_from_matrix(matrix, encoding=encoding, delimiter=delimiter)


def _clean_number(value: Any) -> Tuple[float, bool, bool]:
    """返回 (数值, 是否有效, 是否属于空值占位符)。"""
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
    for symbol in ("$", "€", "£", "¥", "￥", ",", "%", " "):
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


def _as_int(value: float) -> int:
    return int(round(float(value or 0.0)))


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "")
    return re.sub(r"\s+", " ", text).strip()


def _infer_currency(rows: Iterable[Dict[str, Any]]) -> str:
    values: List[str] = []
    for row in rows:
        for value in row.values():
            values.append(_cell_text(value))
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


def normalize_and_aggregate(headers, rows) -> dict:
    """清洗数值并按 (campaign, ad_group, search_term, match_type) 聚合。"""
    headers = [_cell_text(header) for header in (headers or [])]
    mapped = map_columns(headers)
    missing = [field for field in ("search_term", "spend") if not mapped.get(field)]
    if missing:
        actual = [header for header in headers if str(header).strip()][:12]
        missing_label = "、".join(missing)
        actual_label = "、".join(actual) if actual else "（无表头）"
        raise ValueError(
            f"缺少必要列：{missing_label}。你报表里实际有哪些列名（最多 12 个）：{actual_label}"
        )

    source_rows = [row for row in (rows or []) if isinstance(row, dict) and not _row_is_blank(list(row.values()))]
    warnings: List[str] = []
    truncated = len(source_rows) > _MAX_ROWS
    if truncated:
        source_rows = source_rows[:_MAX_ROWS]
        warnings.append(f"报表共 {len(rows or [])} 行，仅分析前 50000 行。")

    currency = _infer_currency(source_rows)
    warning_columns: List[str] = []
    skipped_without_term = 0
    aggregated: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for row in source_rows:
        values: Dict[str, Any] = {}
        row_invalid = False
        for canonical in CANONICAL_FIELDS:
            original = mapped.get(canonical)
            raw_value = row.get(original, "") if original else ""
            if canonical in _NUMERIC_FIELDS:
                number, valid, _empty = _clean_number(raw_value)
                if not valid:
                    row_invalid = True
                    if original and original not in warning_columns and len(warning_columns) < _MAX_WARNING_COLUMNS:
                        warning_columns.append(original)
                values[canonical] = number
            elif canonical == "match_type":
                values[canonical] = _clean_text(raw_value).upper()
            else:
                values[canonical] = _clean_text(raw_value)

        if row_invalid and warning_columns:
            # 具体提示在循环结束后统一生成，避免同一列重复刷屏。
            pass
        if not values.get("search_term"):
            skipped_without_term += 1
            continue

        for field in ("impressions", "clicks", "orders"):
            values[field] = _as_int(values.get(field, 0))
        values["spend"] = round(float(values.get("spend") or 0.0), 2)
        values["sales"] = round(float(values.get("sales") or 0.0), 2)

        key = (
            values.get("campaign", ""),
            values.get("ad_group", ""),
            values.get("search_term", ""),
            values.get("match_type", ""),
        )
        if key not in aggregated:
            aggregated[key] = dict(values)
        else:
            target = aggregated[key]
            for field in _NUMERIC_FIELDS:
                target[field] = float(target.get(field) or 0.0) + float(values.get(field) or 0.0)
            for field in ("impressions", "clicks", "orders"):
                target[field] = _as_int(target[field])
            target["spend"] = round(float(target["spend"]), 2)
            target["sales"] = round(float(target["sales"]), 2)

    for column in warning_columns:
        warnings.append(f"列「{column}」含无法识别的数值，已按 0 处理。")
    if skipped_without_term:
        warnings.append(f"有 {skipped_without_term} 行没有搜索词，已跳过。")

    records = list(aggregated.values())
    return {
        "records": records,
        "rows_total": len(source_rows),
        "rows_after_aggregate": len(records),
        "columns_mapped": mapped,
        "currency": currency,
        "warnings": warnings,
    }


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _money_symbol(currency: str) -> str:
    return {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£"}.get(str(currency).upper(), "$")


def _format_money(value: float, currency: str) -> str:
    return f"{_money_symbol(currency)}{_as_float(value):,.2f}"


def _record_metrics(record: Dict[str, Any], breakeven_acos: float) -> Dict[str, float]:
    spend = _as_float(record.get("spend"))
    sales = _as_float(record.get("sales"))
    clicks = _as_int(_as_float(record.get("clicks")))
    orders = _as_int(_as_float(record.get("orders")))
    acos = spend / sales * 100.0 if sales > 0 else 0.0
    loss = max(0.0, spend - sales * breakeven_acos / 100.0) if orders > 0 else 0.0
    return {
        "spend": spend,
        "sales": sales,
        "clicks": float(clicks),
        "orders": float(orders),
        "acos": acos,
        "loss": loss,
    }


def audit_report(records, gross_margin_pct=30.0, currency="unknown") -> dict:
    """对聚合后的 canonical records 做确定性失血审计。"""
    breakeven_acos = round(_as_float(gross_margin_pct), 2)
    records = list(records or [])

    total_spend = 0.0
    total_sales = 0.0
    total_orders = 0
    total_clicks = 0
    total_impressions = 0
    zero_conversion_spend = 0.0
    zero_conversion_terms = 0
    negative_margin_loss = 0.0
    negative_margin_terms = 0
    waste_candidates: List[Dict[str, Any]] = []
    quadrant_stats = {
        "golden": {"count": 0, "spend": 0.0},
        "scaling": {"count": 0, "spend": 0.0},
        "bleeding": {"count": 0, "spend": 0.0},
        "zero": {"count": 0, "spend": 0.0},
    }

    for record in records:
        metrics = _record_metrics(record, breakeven_acos)
        spend = metrics["spend"]
        sales = metrics["sales"]
        clicks = int(metrics["clicks"])
        orders = int(metrics["orders"])
        acos = metrics["acos"]
        loss = metrics["loss"]

        total_spend += spend
        total_sales += sales
        total_orders += orders
        total_clicks += clicks
        total_impressions += _as_int(_as_float(record.get("impressions")))

        is_zero = orders == 0 and spend > 0
        is_negative = orders > 0 and loss > 0
        if is_zero:
            zero_conversion_spend += spend
            zero_conversion_terms += 1
        if is_negative:
            negative_margin_loss += loss
            negative_margin_terms += 1

        if is_zero:
            quadrant_stats["zero"]["count"] += 1
            quadrant_stats["zero"]["spend"] += spend
        elif orders > 0 and acos <= breakeven_acos:
            quadrant_stats["golden"]["count"] += 1
            quadrant_stats["golden"]["spend"] += spend
        elif orders > 0 and acos <= breakeven_acos * 1.5:
            quadrant_stats["scaling"]["count"] += 1
            quadrant_stats["scaling"]["spend"] += spend
        elif orders > 0:
            quadrant_stats["bleeding"]["count"] += 1
            quadrant_stats["bleeding"]["spend"] += spend

        if is_zero or is_negative:
            if is_zero:
                reason = f"{clicks} 次点击 0 单，白烧 {_format_money(spend, currency)}"
                action = "加 Negative Exact"
            else:
                reason = (
                    f"{clicks} 次点击 {orders} 单，ACOS {acos:.2f}% 高于保本 "
                    f"{breakeven_acos:.2f}%，倒挂 {_format_money(loss, currency)}"
                )
                action = "降价 30% 或加否"
            waste_candidates.append(
                {
                    "search_term": _clean_text(record.get("search_term")),
                    "campaign": _clean_text(record.get("campaign")),
                    "ad_group": _clean_text(record.get("ad_group")),
                    "match_type": _clean_text(record.get("match_type")),
                    "clicks": clicks,
                    "spend": round(spend, 2),
                    "orders": orders,
                    "sales": round(sales, 2),
                    "acos": round(acos, 2),
                    "reason": reason,
                    "action": action,
                }
            )

    waste_candidates.sort(
        key=lambda item: (
            -_as_float(item.get("spend")),
            -_as_int(_as_float(item.get("clicks"))),
            str(item.get("search_term") or ""),
        )
    )
    top_waste_terms = waste_candidates[:15]

    acos = total_spend / total_sales * 100.0 if total_sales > 0 else 0.0
    roas = total_sales / total_spend if total_spend > 0 else 0.0
    cvr = total_orders / total_clicks * 100.0 if total_clicks > 0 else 0.0
    cpc = total_spend / total_clicks if total_clicks > 0 else 0.0
    total_waste = zero_conversion_spend + negative_margin_loss
    waste_ratio = total_waste / total_spend if total_spend > 0 else 0.0

    if waste_ratio < 0.10:
        grade, grade_label = "A", "优秀"
    elif waste_ratio < 0.20:
        grade, grade_label = "B", "可控但有漏洞"
    elif waste_ratio < 0.32:
        grade, grade_label = "C", "明显失血"
    else:
        grade, grade_label = "D", "严重失血"

    negatives = build_negatives(records)
    quadrant_labels = {
        "golden": "🌟 高投产金牛词",
        "scaling": "📈 可放量词",
        "bleeding": "🩸 高 ACOS 失血词",
        "zero": "🕳️ 零转化空耗词",
    }
    quadrants = [
        {
            "key": key,
            "label": quadrant_labels[key],
            "count": int(quadrant_stats[key]["count"]),
            "spend": round(float(quadrant_stats[key]["spend"]), 2),
        }
        for key in ("golden", "scaling", "bleeding", "zero")
    ]

    return {
        "rows_total": len(records),
        "rows_after_aggregate": len(records),
        "currency": str(currency or "unknown"),
        "columns_mapped": {},
        "breakeven_acos": breakeven_acos,
        "totals": {
            "spend": round(total_spend, 2),
            "sales": round(total_sales, 2),
            "orders": total_orders,
            "clicks": total_clicks,
            "impressions": total_impressions,
            "acos": round(acos, 2),
            "roas": round(roas, 2),
            "cvr": round(cvr, 2),
            "cpc": round(cpc, 2),
        },
        "waste": {
            "zero_conversion_spend": round(zero_conversion_spend, 2),
            "zero_conversion_terms": zero_conversion_terms,
            "negative_margin_loss": round(negative_margin_loss, 2),
            "negative_margin_terms": negative_margin_terms,
            "waste_ratio": round(waste_ratio, 2),
        },
        "recoverable": {
            "immediate": round(zero_conversion_spend * 0.9, 2),
            "conservative_total": round(
                zero_conversion_spend * 0.9 + negative_margin_loss * 0.7, 2
            ),
            "assumption": "零转化空耗保留 10% 品牌防守词；ACOS 倒挂部分按降价后 70% 可追回",
        },
        "grade": grade,
        "grade_label": grade_label,
        "top_waste_terms": top_waste_terms,
        "quadrants": quadrants,
        "negatives_count": int(negatives["count"]),
        "negatives_truncated": bool(negatives["truncated"]),
        "negatives_txt": str(negatives["txt"]),
        "negatives_csv": str(negatives["csv"]),
        "warnings": [],
    }


def _clean_negative_field(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _cell_text(value)).replace("\ufeff", "")
    text = text.replace('"', "'")
    text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def build_negatives(records, min_spend=1.0, max_terms=200) -> dict:
    """生成亚马逊批量操作可回传的 Negative Exact TXT / CSV。"""
    records = list(records or [])
    candidates: List[Dict[str, Any]] = []
    threshold = _as_float(min_spend)
    for record in records:
        spend = _as_float(record.get("spend"))
        orders = _as_int(_as_float(record.get("orders")))
        term = _clean_negative_field(record.get("search_term"))
        if not term or orders != 0 or spend < threshold:
            continue
        candidates.append(
            {
                "campaign": _clean_negative_field(record.get("campaign")),
                "ad_group": _clean_negative_field(record.get("ad_group")),
                "search_term": term,
                "clicks": _as_int(_as_float(record.get("clicks"))),
                "spend": spend,
            }
        )
    candidates.sort(
        key=lambda item: (
            -_as_float(item["spend"]),
            -int(item["clicks"]),
            item["search_term"],
            item["campaign"],
            item["ad_group"],
        )
    )
    limit = max(0, _as_int(_as_float(max_terms)))
    truncated = len(candidates) > limit
    emitted = candidates[:limit]

    txt_lines = [
        "\t".join(
            (
                "Sponsored Products",
                "Negative Keyword",
                "Create",
                item["campaign"],
                item["ad_group"],
                item["search_term"],
                "negativeExact",
                "enabled",
            )
        )
        for item in emitted
    ]
    txt = "\n".join(txt_lines)
    if txt_lines:
        txt += "\n"

    csv_buffer = io.StringIO()
    csv_buffer.write("\ufeff")
    writer = csv.writer(csv_buffer, lineterminator="\n")
    writer.writerow(("Campaign", "Ad Group", "Negative Keyword", "Match Type", "Clicks", "Spend"))
    for item in emitted:
        writer.writerow(
            (
                item["campaign"],
                item["ad_group"],
                item["search_term"],
                "negativeExact",
                int(item["clicks"]),
                f"{_as_float(item['spend']):.2f}",
            )
        )

    return {"txt": txt, "csv": csv_buffer.getvalue(), "count": len(emitted), "truncated": truncated}


_HIGH_SPEND_ZERO_TERMS = (
    "wireless earbuds",
    "充电器 快充",
    "earbuds, waterproof",
    '"premium" phone case',
    "bluetooth headphones",
    "防摔手机壳",
)
_HIGH_SPEND_ZERO_VALUES = (24.5, 18.9, 16.4, 14.2, 12.8, 11.1)
_HIGH_SPEND_ZERO_CLICKS = (61, 38, 45, 55, 44, 29)
_HIGH_SPEND_ZERO_IMPRESSIONS = (1380, 820, 1090, 760, 930, 640)

_ZERO_TERMS = (
    "noise cancelling earbuds",
    "gaming headset",
    "type c charger",
    "magsafe case",
    "running phone holder",
    "waterproof pouch",
    "headphone adapter",
    "bluetooth receiver",
    "car charger usb",
    "mini speaker",
    "wireless charging pad",
    "phone ring holder",
    "usb hub",
    "repair kit",
    "screen protector",
    "cable organizer",
    "desk stand",
    "travel adapter",
)

_INVERTED_TERMS = (
    "portable charger 10000mah",
    "sport earbuds",
    "charging dock",
    "phone lanyard",
    "audio splitter",
    "wireless mouse",
    "laptop stand",
    "usb microphone",
    "smart watch band",
)
_INVERTED_SPEND = (2.1, 2.8, 3.2, 3.6, 4.1, 4.7, 5.2, 5.8, 6.4)
_INVERTED_SALES = (6.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0)
_INVERTED_ORDERS = (1, 1, 1, 1, 2, 2, 2, 2, 3)
_INVERTED_CLICKS = (8, 10, 12, 14, 16, 18, 20, 22, 24)

_HEALTHY_TERMS = (
    "phone case clear",
    "fast charger",
    "earbuds case",
    "braided usb cable",
    "tablet stand",
    "power bank slim",
    "wired earphones",
    "charging cable 2m",
    "foldable stand",
    "car mount",
    "speaker mini",
    "screen protector tempered glass",
    "usb c adapter",
    "bluetooth dongle",
    "phone grip",
    "wireless keyboard",
    "mouse pad",
    "laptop sleeve",
    "travel charger",
    "audio cable",
    "charging station",
    "smart watch charger",
    "usb c hub",
    "phone tripod",
    "earbud tips",
    "cable clip",
    "desk lamp",
)
_HEALTHY_SPEND_CYCLE = (4.2, 5.1, 6.3, 7.2, 8.1, 9.4, 10.2)
_CAMPAIGNS = ("Campaign Alpha", "Campaign Beta", "Campaign Gamma")
_AD_GROUPS = ("Earbuds", "Charging", "Phone Accessories", "Audio")


def _sample_terms() -> List[str]:
    terms = list(_HIGH_SPEND_ZERO_TERMS) + list(_ZERO_TERMS) + list(_INVERTED_TERMS) + list(_HEALTHY_TERMS)
    if len(terms) != 60:
        raise AssertionError("样例搜索词必须恰好 60 个")
    return terms


def sample_report_csv() -> str:
    """生成 60 行合成搜索词报表（非真实卖家数据），输出逐字节确定。"""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    output.write("Sponsored Products Search term report\n\n")
    writer.writerow(
        (
            "Campaign Name ",
            "Ad Group Name ",
            "Customer Search Term ",
            "Match Type ",
            "Impressions ",
            "Clicks ",
            "Spend ",
            "7 Day Total Sales ",
            "7 Day Total Orders ",
        )
    )

    terms = _sample_terms()
    for index, term in enumerate(terms):
        campaign = _CAMPAIGNS[index % len(_CAMPAIGNS)]
        ad_group = _AD_GROUPS[index % len(_AD_GROUPS)]
        match_type = ("EXACT", "PHRASE", "BROAD")[index % 3]

        if index < 6:
            spend = _HIGH_SPEND_ZERO_VALUES[index]
            clicks = _HIGH_SPEND_ZERO_CLICKS[index]
            impressions = _HIGH_SPEND_ZERO_IMPRESSIONS[index]
            sales = 0.0
            orders = 0
        elif index < 24:
            zero_index = index - 6
            spend = 1.0
            clicks = 3 + (zero_index % 6)
            impressions = 140 + zero_index * 31
            sales = 0.0
            orders = 0
        elif index < 33:
            inverted_index = index - 24
            spend = _INVERTED_SPEND[inverted_index]
            clicks = _INVERTED_CLICKS[inverted_index]
            impressions = 180 + inverted_index * 47
            sales = _INVERTED_SALES[inverted_index]
            orders = _INVERTED_ORDERS[inverted_index]
        else:
            healthy_index = index - 33
            spend = _HEALTHY_SPEND_CYCLE[healthy_index % len(_HEALTHY_SPEND_CYCLE)]
            clicks = 5 + (healthy_index * 3) % 20
            impressions = 160 + healthy_index * 53
            sales = round(spend * (3.5 + (healthy_index % 4) * 0.4), 2)
            orders = 1 + healthy_index % 3

        writer.writerow(
            (
                campaign,
                ad_group,
                term,
                match_type,
                impressions,
                clicks,
                f"{spend:.2f}",
                f"{sales:.2f}",
                orders,
            )
        )
    return output.getvalue()
