"""
热力图 Heatmap - 矩阵图表
图表分类: 矩阵 Matrix | 书章节: Ch8
感知排名: ★★★★☆

统一接口:
    generate(df, mapping, options) -> ChartResult
"""
import logging
log = logging.getLogger(__name__)
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from charts.base import ChartResult

__all__ = ["generate"]

_DATA_FMT = "row列 + col列 + value列（三列长格式）或 宽格式(行标签 + 多个数值列)"
_DESC = "用颜色深浅表示数值大小，适合展示矩阵数据和相关性。支持宽格式数据自动转换。"


def _auto_col(df: pd.DataFrame, *hints: str) -> Optional[str]:
    """根据提示自动查找匹配的列名。
    
    策略：
    1. 精确匹配 hints 中的任何一个
    2. 模糊匹配（包含关系）
    3. 类型匹配：根据 hint 的语义推断类型
    4. 自动推断：无 hints 时返回第一个合适的列
    """
    strs = [c for c in df.columns if df[c].dtype == object or df[c].dtype == 'string']
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    col_lower = {c.lower(): c for c in df.columns}
    
    # 1. 精确匹配 hints
    for h in hints:
        h_lower = h.lower()
        if h_lower in col_lower:
            return col_lower[h_lower]
    
    # 2. 模糊匹配（包含关系）
    for h in hints:
        h_lower = h.lower()
        for col in df.columns:
            col_lower_name = col.lower()
            if h_lower in col_lower_name or col_lower_name in h_lower:
                return col
    
    # 3. 类型匹配：根据 hint 的语义推断应该是什么类型
    if hints:
        hint = hints[0].lower()
        # 字符串类型的 hints
        if any(kw in hint for kw in ["source", "target", "label", "name", "group", "category", "phase", "row", "col", "path", "text", "word", "location", "geo"]):
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        # 数值类型的 hints
        elif any(kw in hint for kw in ["value", "size", "amount", "count", "frequency", "score", "rank", "actual", "target", "range"]):
            if nums:
                return nums[0]
            if strs:
                return strs[0]
        # 通用的 x/y：x 通常是类别（字符串），y 通常是数值
        elif hint == "x":
            if strs:
                return strs[0]
            if nums:
                return nums[0]
        elif hint == "y":
            if nums:
                return nums[0]
            if strs:
                return strs[0]
    
    # 4. 无 hints 时自动推断
    if not hints:
        if strs:
            return strs[0]
        if nums:
            return nums[0]
    
    return None


def _detect_wide_format(df: pd.DataFrame) -> bool:
    """检测是否为宽格式数据（1个字符串列 + 多个数值列）"""
    strs = [c for c in df.columns if df[c].dtype == object or df[c].dtype == 'string']
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    
    # 宽格式特征：恰好1个字符串列，2个以上数值列
    return len(strs) == 1 and len(nums) >= 2


def _convert_wide_to_matrix(df: pd.DataFrame, id_col: str) -> tuple:
    """将宽格式转换为矩阵格式
    
    输入：
        行标签  15    16    17    18    19
        标签二  36.39 31.97 29.03 28.11 25.46
        标签三  49.72 44.48 35.79 35.32 30.22
    
    输出：
        z: 矩阵数据
        y: 行标签列表
        x: 列标签列表
    """
    # 获取所有数值列（按原始顺序）
    value_cols = [c for c in df.columns if c != id_col and pd.api.types.is_numeric_dtype(df[c])]
    
    # 设置行标签为索引
    df_matrix = df.set_index(id_col)[value_cols]
    
    # 返回矩阵数据、行标签、列标签
    return df_matrix.values, df_matrix.index.tolist(), df_matrix.columns.tolist()


def _build_html(title: str, chart_name: str, library: str,
                data_fmt: str, desc: str, embed: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title>
<style>
body{{font-family:"Heiti SC","Microsoft YaHei",sans-serif;margin:40px;background:#fafafa}}
.chart-wrap{{background:white;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:24px;margin-bottom:32px}}
h1{{color:#222;font-size:22px;margin-bottom:6px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:24px}}
.desc{{color:#555;font-size:14px;line-height:1.7;margin-top:20px}}
</style></head>
<body><div class="chart-wrap">
<h1>{title}</h1><div class="subtitle">{chart_name} | {library}</div>
{embed}
</div><div class="desc">
<strong>数据格式：</strong>{data_fmt}<br>
<strong>说明：</strong>{desc}
</div></body></html>"""


def generate(
    df: pd.DataFrame = None,
    mapping: Dict[str, str] = None,
    options: Dict[str, Any] = None,
    # ── 向后兼容旧接口 ──────────────────────────────
    excel_path: str = None,
    row: str = "row",
    col: str = "col",
    value: str = "value",
    title: str = "热力图",
    **kwargs
) -> ChartResult:
    warnings: list = []
    options = options or {}
    mapping = mapping or {}

    if df is None:
        if excel_path:
            try:
                df = pd.read_excel(excel_path)
            except Exception as e:
                log.warning("[chart] 图表生成异常: %s", e)
                return ChartResult(warnings=[f"读取Excel失败: {e}"])
        else:
            return ChartResult(warnings=["请提供 df 或 excel_path"])

    # Support both (row/col/value) and (x/y/z) mapping keys.
    # y → row axis (True Label), x → col axis (Predicted Label), z → value.
    row_col = mapping.get("row") or mapping.get("y") or row
    col_col = mapping.get("col") or mapping.get("x") or col
    val_col = mapping.get("value") or mapping.get("z") or value
    title = options.get("title", title)

    # ── 检测并转换宽格式数据 ──────────────────────────────
    if _detect_wide_format(df):
        # 找到字符串列作为 id_col
        id_col = [c for c in df.columns if df[c].dtype == object or df[c].dtype == 'string'][0]
        z_data, y_labels, x_labels = _convert_wide_to_matrix(df, id_col)
        
        warnings.append(f"自动转换宽格式数据为矩阵：{id_col} (行) × 周期 (列)")
        
        # 创建热力图
        fig = go.Figure(data=go.Heatmap(
            z=z_data,
            y=y_labels,
            x=x_labels,
            colorscale="Blues",
            text=z_data,
            texttemplate="%{text:.2f}",
            textfont={"size": 10},
            hovertemplate="行: %{y}<br>列: %{x}<br>值: %{z:.2f}<extra></extra>"
        ))
        
        fig.update_layout(
            title=dict(text=title, x=0.02, xanchor="left", font=dict(size=18, color="#1F1F1F")),
            font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
            xaxis_title=col_col,
            yaxis_title=row_col,
            margin=dict(l=80, r=40, t=70, b=70),
            height=500,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
    else:
        # 长格式数据：正常处理
        _row = _auto_col(df, row_col, "row", "产品", "类别", "y", "行标签")
        _col = _auto_col(df, col_col, "col", "区域", "x", "周期")
        _val = _auto_col(df, val_col, "value", "销售额", "销量", "amount", "num", "数值")

        for role, col_ in [("row", _row), ("col", _col), ("value", _val)]:
            if col_ is None or col_ not in df.columns:
                warnings.append(f"找不到必填字段 [{role}]")

        if _row not in df.columns or _col not in df.columns or _val not in df.columns:
            return ChartResult(warnings=warnings)

        # 转换为透视表（矩阵）
        pivot_df = df.pivot_table(
            values=_val, index=_row, columns=_col,
            aggfunc='sum', fill_value=0
        )
        pivot_df.columns = [str(c) for c in pivot_df.columns]
        pivot_df.index   = [str(i) for i in pivot_df.index]

        # 坐标轴标签：直接用实际列名
        x_label = _col
        y_label = _row

        z_vals = pivot_df.values.tolist()
        y_vals = pivot_df.index.tolist()
        x_vals = pivot_df.columns.tolist()

        # 数值显示格式：整数用 %{text:.0f}，小数用 %{text:.2f}，自动判断
        import numpy as _np
        _flat = [v for row_ in z_vals for v in row_ if v is not None]
        _all_int = all(float(v) == int(v) for v in _flat) if _flat else True
        _texttemplate = "%{text:.0f}" if _all_int else "%{text:.2f}"

        fig = go.Figure(data=go.Heatmap(
            z=z_vals,
            y=y_vals,
            x=x_vals,
            colorscale="Blues",
            text=z_vals,
            texttemplate=_texttemplate,
            textfont={"size": 11},
            hovertemplate=f"{y_label}: %{{y}}<br>{x_label}: %{{x}}<br>值: %{{z:.3g}}<extra></extra>"
        ))

        fig.update_layout(
            title=dict(text=title, x=0.02, xanchor="left", font=dict(size=18, color="#1F1F1F")),
            font=dict(family="Arial, Helvetica, sans-serif", size=12, color="#1F1F1F"),
            xaxis=dict(
                title=x_label,
                side="bottom",
            ),
            yaxis=dict(
                title=y_label,
                autorange="reversed",
            ),
            margin=dict(l=80, r=40, t=70, b=70),
            height=500,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )

    chart_html = pio.to_html(fig, full_html=False, include_plotlyjs=False)
    html = _build_html(title, "heatmap", "plotly", _DATA_FMT, _DESC, chart_html)


    meta = {
        "chart_id": "heatmap",
        "n_rows": len(df),
    }

    return ChartResult(html=html, spec={}, warnings=warnings, meta=meta)
