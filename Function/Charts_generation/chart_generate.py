#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chart Generate - 调用 charts 目录中的实际图表生成模块"""
import sys
from pathlib import Path
import pandas as pd
from typing import Dict, List, Optional, Any
import logging
import importlib

logger = logging.getLogger(__name__)

CHART_PROJECT = Path(__file__).parent
sys.path.insert(0, str(CHART_PROJECT))

# ── 本地静态资源路径（Flask 服务，通过 /static/vendor/ 访问）──────────────
_PLOTLY_LOCAL = "/static/vendor/plotly.min.js"
_PLOTLY_CDN   = "https://cdn.plot.ly/plotly-"          # 前缀，用于检测
_PLOTLY_TAG   = f"<script src='{_PLOTLY_LOCAL}'></script>"


def _chart_key(chart_type: str) -> str:
    return str(chart_type or "").strip().lower()


def _unwrap_item_dicts(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap {"item": [...]} dicts that some LLM serializers produce.

    Certain function-calling adapters convert XML-style arrays
    into {"y": {"item": ["col1"]}} instead of {"y": ["col1"]}.
    This unwraps such structures so downstream logic sees plain lists.
    """
    result = {}
    for key, value in (mapping or {}).items():
        if isinstance(value, dict):
            if "item" in value and isinstance(value["item"], (list, tuple)):
                result[key] = list(value["item"])
                continue
            if "item" in value and isinstance(value["item"], str):
                result[key] = value["item"]
                continue
            list_vals = [v for v in value.values() if isinstance(v, (list, tuple))]
            if list_vals:
                result[key] = list(list_vals[0])
                continue
            result[key] = value
        else:
            result[key] = value
    return result


def _normalize_chart_mapping(
    chart_type: str,
    mapping: Dict[str, Any],
    columns=None,
) -> Dict[str, Any]:
    """Normalize common LLM field-mapping aliases before chart dispatch."""
    # Unwrap {"item": [...]} wrapping from certain LLM serializers BEFORE
    # any str() coercion or comma-split logic sees it.
    normalized = _unwrap_item_dicts(mapping or {})
    chart_key = _chart_key(chart_type)
    column_names = {str(col) for col in ([] if columns is None else columns)}

    # Registry roles use singular names. Some models still emit common plotting
    # aliases; normalize those centrally instead of making every chart support
    # the same typo/variant independently.
    aliases = {
        "categories": "x",
        "labels": "label",
        "values": "value",
    }
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized[alias]

    # Models occasionally serialize a column list as one comma-separated
    # string. Only split when the full string is not itself a real column name.
    for key in ("y", "value_cols", "dimensions"):
        value = normalized.get(key)
        if (
            isinstance(value, str)
            and "," in value
            and value not in column_names
        ):
            normalized[key] = [part.strip() for part in value.split(",") if part.strip()]

    # Wide tables sometimes arrive as series=[col1, col2, ...]. In all three
    # charts a list means value columns, not the name of a grouping column.
    series = normalized.get("series")
    if isinstance(series, (list, tuple, set)):
        series_items = list(series)
        series_cols = [str(item) for item in series_items if isinstance(item, str)]

        # A list of {name, color} objects is presentation metadata, not a field
        # mapping. Ignore it; the actual columns must come from y/value_cols.
        if len(series_cols) == len(series_items):
            if chart_key in {"line_chart", "area_chart", "stacked_area_chart"}:
                normalized.setdefault("y", series_cols)
            else:
                normalized.setdefault("value_cols", series_cols)
        normalized.pop("series", None)

    # Multi-series bar charts accept y=[...] as wide-format shorthand.
    y_value = normalized.get("y")
    if chart_key in {"grouped_bar_chart", "stacked_bar_chart"} and isinstance(
        y_value, (list, tuple, set)
    ):
        normalized.setdefault("value_cols", list(y_value))

    # Wide-format bars are defined by x + value_cols. Ignore invented long-
    # format fields and literal color strings that are not SQL result columns;
    # otherwise the module may silently render only the first numeric column.
    if chart_key in {"grouped_bar_chart", "stacked_bar_chart"} and normalized.get("value_cols"):
        for key in ("series", "color"):
            value = normalized.get(key)
            if isinstance(value, str) and value not in column_names:
                normalized.pop(key, None)

    return normalized


def _inject_plotly(html: str) -> str:
    """确保 HTML 内有本地 plotly 脚本，且脚本在所有 inline <script> 之前加载。

    三种场景：
    1. 已有 CDN plotly 外链（include_plotlyjs="cdn" 模式）→ 替换为本地路径。
    2. 已有内联 plotly bundle（include_plotlyjs=True 模式）→ 不处理，保持原样。
    3. 没有任何 plotly 脚本（include_plotlyjs=False 模式）→ 在 <head> 里注入本地
       外链，并把所有 inline Plotly.newPlot/react 调用包裹进 DOMContentLoaded
       回调，确保库加载完毕后再执行，消除竞态。
    """
    import re

    # 场景 1：替换 CDN 外链为本地路径
    html = re.sub(
        r"<script[^>]+src=['\"]https?://cdn\.plot\.ly/plotly-[^'\"]+['\"][^>]*></script>",
        _PLOTLY_TAG,
        html,
    )
    html = re.sub(
        r"<script[^>]+src=['\"]https?://cdn\.jsdelivr\.net/npm/plotly\.js[^'\"]*['\"][^>]*></script>",
        _PLOTLY_TAG,
        html,
    )

    # 场景 2/3：检查是否已经有 plotly 脚本（外链 src 或内联 bundle）
    has_plotly_src    = bool(re.search(r"<script[^>]+plotly", html, re.IGNORECASE))
    has_plotly_inline = "Plotly=" in html or "window.Plotly" in html  # 内联 bundle 特征

    if not has_plotly_src and not has_plotly_inline:
        # 场景 3：库缺失。
        # 把外链注入到 </head> 前（或 <body> 前），保证在 inline script 之前被
        # 解析器看到；同时把所有 inline Plotly.* 调用包进 DOMContentLoaded，
        # 消除外链脚本尚未执行时 inline script 就运行的竞态。
        if "</head>" in html:
            html = html.replace("</head>", f"{_PLOTLY_TAG}\n</head>", 1)
        elif "<body" in html:
            html = re.sub(r"<body[^>]*>",
                          lambda m: m.group(0) + f"\n{_PLOTLY_TAG}", html, count=1)
        else:
            html = _PLOTLY_TAG + "\n" + html

        # 把 inline Plotly.newPlot / Plotly.react 包进 DOMContentLoaded，
        # 确保外链 plotly.min.js 执行完毕后再调用。
        # 匹配：<script>...Plotly.newPlot(...)...</script>（不含 src 属性）
        def _wrap_inline(m: re.Match) -> str:
            body = m.group(1)
            if "Plotly." not in body:
                return m.group(0)
            return (
                "<script>"
                "document.addEventListener('DOMContentLoaded',function(){"
                + body +
                "});"
                "</script>"
            )

        html = re.sub(
            r"<script(?![^>]+\bsrc\b)[^>]*>([\s\S]*?)</script>",
            _wrap_inline,
            html,
        )

    return html


# Injected into every chart HTML so it renders cleanly inside a dashboard iframe.
_EMBED_STYLE = (
    "<style>"
    "html,body{margin:0!important;padding:0!important;overflow:hidden!important;"
    "background:transparent!important;}"
    ".chart-wrap{margin:8px!important;}"
    ".plotly-graph-div[style*='height:100%']{min-height:360px!important;}"
    ".desc{display:none!important;}"          # hide the data-format footer in iframes
    "</style>"
)

def _inject_embed_style(html: str) -> str:
    """Prepend embed-mode CSS so chart pages look clean inside dashboard iframes."""
    if "</head>" in html:
        return html.replace("</head>", _EMBED_STYLE + "\n</head>", 1)
    # Fallback: prepend to body
    return html.replace("<body", _EMBED_STYLE + "\n<body", 1)

try:
    from charts.base import ChartResult, FieldMapping
except ImportError as e:
    logger.error(f"Failed to import charts.base: {e}")
    # 定义备用类
    class ChartResult:
        def __init__(self, html="", spec=None, warnings=None, meta=None):
            self.html = html
            self.spec = spec or {}
            self.warnings = warnings or []
            self.meta = meta or {}
        def is_valid(self):
            return bool(self.html.strip()) and len(self.html) > 500
    
    class FieldMapping:
        pass


def _iter_mapping_columns(mapping: Dict[str, Any]):
    for role, value in (mapping or {}).items():
        if isinstance(value, str):
            yield str(role), value
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str):
                    yield str(role), item
        elif isinstance(value, dict):
            # Unwrap {"item": [...]} or similar dict wrapping
            for sub in value.values():
                if isinstance(sub, (list, tuple)):
                    for item in sub:
                        if isinstance(item, str):
                            yield str(role), item
                elif isinstance(sub, str):
                    yield str(role), sub


def _validate_mapping_columns(mapping: Dict[str, Any], columns) -> Optional[str]:
    column_names = {str(col) for col in columns}
    missing = sorted({
        col
        for role, col in _iter_mapping_columns(mapping)
        if col not in column_names and role not in {"series", "color", "group"}
    })
    if missing:
        return "field_mapping references missing SQL result columns: " + ", ".join(missing)
    return None


def _required_mapping_error(chart_type: str, mapping: Dict[str, Any]) -> Optional[str]:
    chart_key = _chart_key(chart_type)
    if chart_key in {"bar_chart", "line_chart"}:
        required = ("x", "y")
    elif chart_key in {"grouped_bar_chart", "stacked_bar_chart"}:
        required = ("x",)
    else:
        return None
    missing = [role for role in required if not mapping.get(role)]
    if missing:
        return "field_mapping missing required role(s): " + ", ".join(missing)
    return None


def generate_chart(
    df: pd.DataFrame = None,
    excel_path: str = None,
    chart_type: str = "bar_chart",
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    color_scheme: str = "mckinsey",
    **kwargs
) -> Dict[str, Any]:
    """生成图表 - 调用 charts 目录中的实际模块"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None or df.empty:
            return {"error": "No data"}
        
        logger.info(f"Generating {chart_type} with {len(df)} rows, {len(df.columns)} columns")

        # ✅ 统一列名类型：避免 heatmap 等图表里对列名做 .lower() 时遇到 int
        df = df.copy()
        df.columns = df.columns.map(str)
        
        # 动态导入图表模块
        try:
            module = importlib.import_module(f"charts.{chart_type}")
            generate_func = getattr(module, "generate", None)
            
            if not generate_func:
                logger.error(f"Module charts.{chart_type} has no generate function")
                return {"error": f"Chart type {chart_type} not found"}
        except ImportError as e:
            logger.error(f"Failed to import charts.{chart_type}: {e}")
            return {"error": f"Chart type {chart_type} not found"}
        
        # 自动检测字段映射
        if not mapping:
            mapping = _auto_detect_mapping(df, chart_type)

        # Unwrap {"item": [...]} wrapping before any str() coercion
        # to prevent dict repr like "{'item': [...]}" being split
        if mapping:
            mapping = _unwrap_item_dicts(mapping)
        # 统一 mapping 中的列名为字符串（mapping 的 value 通常是列名）
        # 但对于列表/dict 类型保持原样
        if mapping:
            mapping = {k: (v if isinstance(v, (list, tuple, dict)) else (str(v) if v is not None else v)) for k, v in mapping.items()}
        mapping = _normalize_chart_mapping(chart_type, mapping or {}, df.columns)
        mapping_error = _validate_mapping_columns(mapping, df.columns)
        if mapping_error:
            return {"error": mapping_error}
        required_error = _required_mapping_error(chart_type, mapping)
        if required_error:
            return {"error": required_error}
        
        # 合并 options，添加 color_scheme
        merged_options = options or {}
        merged_options['color_scheme'] = color_scheme
        
        # 调用图表生成函数
        result = generate_func(df=df, mapping=mapping, options=merged_options)
        
        # 检查返回类型
        if isinstance(result, ChartResult):
            if result.is_valid():
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_plotly(result.html)),
                    "chart_type": chart_type,
                    "warnings": result.warnings,
                    "meta": result.meta
                }
            else:
                html_len = len(result.html.strip())
                logger.error(f"Chart invalid: html length={html_len}, warnings={result.warnings}")
                msg = result.warnings[0] if result.warnings else f"Generated chart is invalid (html={html_len} chars)"
                return {"error": msg}
        elif hasattr(result, "html") and hasattr(result, "warnings"):
            # 模块自带 ChartResult 类（非 charts.base.ChartResult），duck-typing 兼容
            html = result.html or ""
            if html.strip() and len(html) > 500:
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_plotly(html)),
                    "chart_type": chart_type,
                    "warnings": getattr(result, "warnings", []),
                    "meta": getattr(result, "meta", {})
                }
            else:
                html_len = len(html.strip())
                ws = getattr(result, "warnings", [])
                msg = ws[0] if ws else f"Generated chart is invalid (html={html_len} chars)"
                return {"error": msg}
        elif isinstance(result, dict):
            if result.get("html"):
                return {
                    "success": True,
                    "html": _inject_embed_style(_inject_plotly(result.get("html"))),
                    "chart_type": chart_type
                }
            else:
                return {"error": result.get("error", "Unknown error")}
        else:
            return {"error": f"Unexpected result type: {type(result)}"}
    
    except Exception as e:
        logger.error(f"Chart generation error: {e}", exc_info=True)
        return {"error": str(e)}


def _auto_detect_mapping(df: pd.DataFrame, chart_type: str) -> Dict[str, str]:
    """自动检测字段映射"""
    try:
        df = df.copy()
        df.columns = df.columns.map(str)
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
        
        mapping = {}
        chart_key = _chart_key(chart_type)
        
        # 根据图表类型推荐映射
        if chart_key in {"bar_chart", "bar", "grouped_bar", "grouped_bar_chart", "stacked_bar", "stacked_bar_chart"}:
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
                if chart_key in {"grouped_bar", "grouped_bar_chart", "stacked_bar", "stacked_bar_chart"}:
                    if len(string_cols) > 1:
                        mapping["series"] = string_cols[1]
                    elif len(numeric_cols) > 1:
                        mapping["value_cols"] = numeric_cols
        
        elif chart_key == "line_chart":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
        
        elif chart_key == "scatter_plot":
            if len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                if len(numeric_cols) >= 3:
                    mapping["size"] = numeric_cols[2]
        
        elif chart_type == "pie":
            if string_cols and numeric_cols:
                mapping["label"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
        
        elif chart_type == "heatmap":
            if len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                if len(numeric_cols) >= 3:
                    mapping["value"] = numeric_cols[2]
        
        elif chart_type == "histogram_chart":
            if numeric_cols:
                mapping["value"] = numeric_cols[0]
        
        elif chart_type == "boxplot_chart":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif numeric_cols:
                mapping["y"] = numeric_cols[0]
        
        elif chart_type == "violin_chart":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
            elif numeric_cols:
                mapping["y"] = numeric_cols[0]
        
        elif chart_type == "Ridgeline_Plot":
            if string_cols and numeric_cols:
                mapping["group"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
            elif numeric_cols:
                mapping["value"] = numeric_cols[0]
        
        elif chart_type == "Beeswarm_Plot":
            if string_cols and numeric_cols:
                mapping["group"] = string_cols[0]
                mapping["value"] = numeric_cols[0]
            elif numeric_cols:
                mapping["value"] = numeric_cols[0]
        
        elif chart_type == "waterfall":
            if string_cols and numeric_cols:
                mapping["x"] = string_cols[0]
                mapping["y"] = numeric_cols[0]
        
        elif chart_type == "sunburst":
            if len(string_cols) >= 2 and numeric_cols:
                mapping["labels"] = string_cols[0]
                mapping["parents"] = string_cols[1]
                mapping["values"] = numeric_cols[0]
        
        elif chart_type == "treemap":
            if len(string_cols) >= 1 and numeric_cols:
                mapping["labels"] = string_cols[0]
                mapping["values"] = numeric_cols[0]
                if len(string_cols) >= 2:
                    mapping["parents"] = string_cols[1]
        
        elif chart_type == "Parallel_Coordinates_Plot":
            # 平行坐标图：所有数值列作为维度
            if numeric_cols:
                mapping["dimensions"] = numeric_cols
                # 如果有字符串列，用第一个作为color
                if string_cols:
                    mapping["color"] = string_cols[0]
        
        elif chart_type == "Connected_Scatter":
            # 连线散点图：需要x、y、order、size
            if len(numeric_cols) >= 2:
                mapping["x"] = numeric_cols[0]
                mapping["y"] = numeric_cols[1]
                if len(numeric_cols) >= 3:
                    mapping["size"] = numeric_cols[2]
                if string_cols:
                    mapping["order"] = string_cols[0]
        
        logger.debug(f"Auto-detected mapping for {chart_type}: {mapping}")
        return mapping
    
    except Exception as e:
        logger.warning(f"Failed to auto-detect mapping: {e}")
        return {}


def recommend_charts(df: pd.DataFrame = None, excel_path: str = None, limit: int = 5) -> List[Dict]:
    """推荐图表"""
    try:
        if df is None and excel_path:
            df = pd.read_excel(excel_path)
        
        if df is None:
            return []
        
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        string_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
        
        recommendations = []
        
        # 基于数据特征推荐
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "bar_chart", "score": 0.95})
            recommendations.append({"chart_id": "grouped_bar", "score": 0.90})
            recommendations.append({"chart_id": "line_chart", "score": 0.85})
        
        if len(numeric_cols) >= 2:
            recommendations.append({"chart_id": "scatter_plot", "score": 0.80})
            recommendations.append({"chart_id": "heatmap", "score": 0.75})
        
        if string_cols and numeric_cols:
            recommendations.append({"chart_id": "pie", "score": 0.70})
        
        return recommendations[:limit]
    
    except Exception as e:
        logger.error(f"Recommend error: {e}")
        return []
