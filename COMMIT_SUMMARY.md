# Git提交总结 - Agent智能表选择与循环检测优化

## 📋 提交概述

本次提交主要优化了数据分析Agent的智能表选择机制和循环检测系统，修复了多个关键问题，提升了系统稳定性和用户体验。

## 🎯 核心优化内容

### 1. 智能表选择机制优化 (`api/chat.py`)

#### 问题背景
- Agent无法正确识别多数据源的表映射关系
- 用户询问邮件相关问题时，Agent未能识别OA库中的映射
- 跨库查询场景下Agent陷入循环

#### 解决方案

**1.1 多数据源智能路由**
- ✅ 支持跨库查询（同时使用OA库和HR库）
- ✅ 根据问题关键词匹配所有相关数据源
- ✅ 为每个匹配的数据源设置对应的表映射

**1.2 智能表选择逻辑优化**
```python
# 核心逻辑（第674-810行）
def _smart_select_tables_from_question():
    1. 配置自动加载（首次查询时加载_auto_select_enabled标志）
    2. 关键词匹配（从table_mapping中匹配相关表）
    3. 默认表叠加（default_tables始终作为基础表）
    4. 多数据源路由（为所有匹配的数据源设置表）
```

**策略调整**:
- **之前**: 只选择最佳匹配数据源（会清空其他库）
- **现在**: 所有匹配数据源设置表（`default_tables + matched_tables`）
- **原因**: 支持跨库查询需求（如：OA会议 + HR人员）

**配置加载优化**:
```python
# 第707行：首次查询时自动加载配置
if not hasattr(source, "_auto_select_enabled"):
    source._auto_select_enabled = cfg.get("auto_select_tables", False)
```

---

### 2. 循环检测系统重构 (`agent/agent.py`)

#### 问题背景
- Agent执行20+次相同的失败SQL查询但未被停止
- 原循环检测阈值过于严格（3次）
- 窗口内穿插其他工具调用导致检测失效

#### 解决方案

**2.1 循环检测参数可配置化**
```python
# 第1858-1867行：所有阈值改为可配置变量
_MAX_IDENTICAL_CALLS = 10       # 普通工具调用重复阈值
_MAX_METADATA_CALLS = 10        # 元数据查询重复阈值
_MAX_READ_RESULT_CALLS = 10     # read_tool_result重复阈值
_recent_tool_window = 15        # 检测窗口大小
```

**2.2 read_tool_result循环检测修复**
- **之前**: 排除检测（3次阈值，过于严格）
- **现在**: 纳入检测（10次阈值，与其他工具一致）
- **原因**: 用户反馈3次太严格，影响正常使用

**2.3 连续SQL错误检测机制（新增）**

**问题根因分析**:
```
1. SQL执行失败但envelope.ok=True（工具调用本身成功）
2. 失败的SQL被记录到历史
3. 窗口内穿插其他工具调用
4. 同一SQL重复次数不足阈值
→ 循环检测失效
```

**解决方案**:
```python
# 新增变量（第1864-1866行）
_consecutive_sql_errors = 0          # 连续SQL错误计数
_MAX_CONSECUTIVE_SQL_ERRORS = 5      # 最多允许5次连续SQL错误
_last_sql_error = None               # 上次错误的SQL

# 检测逻辑（第4441行后）
if name == "query_data":
    if "SQL Error" in result_str:
        - 标准化SQL用于比较
        - 检查是否与上次错误相同
        - 相同则累加计数，不同则重置
        - 达到5次立即强制停止
        - 错误SQL不计入循环检测历史
```

**核心特性**:
- ✅ 独立于检测窗口，只关注连续性
- ✅ 快速失败（5次连续相同错误即停止）
- ✅ 智能重置（成功或不同错误时重置）
- ✅ 详细反馈（提供错误SQL和可能原因）
- ✅ 不污染循环检测历史

**2.4 调试日志增强**
```python
# 第2757行：循环检测警告日志
if identical_count >= 3:
    log.warning(
        "[loop-detect] WARNING: %d/%d identical calls to '%s'",
        identical_count, threshold, name
    )
```

---

## 📁 修改文件清单

### 核心代码修改
- ✅ `api/chat.py` (+471行) - 智能表选择机制优化
- ✅ `agent/agent.py` (+261行) - 循环检测系统重构

### 新增文档
- ✅ `README_TABLE_SELECTION.md` - 智能表选择机制详细文档
- ✅ `LOOP_DETECTION_FIX.md` - 循环检测修复说明
- ✅ `CONSECUTIVE_SQL_ERROR_DETECTION.md` - SQL错误检测机制文档

### 配置文件
- ✅ `config/default_datasource.json` - 数据源配置示例

---

## 🔧 技术细节

### 智能表选择流程
```
用户提问
    ↓
关键词提取
    ↓
匹配table_mapping（所有数据源）
    ↓
找到匹配？
    ├─ 是 → default_tables + matched_tables
    └─ 否 → 仅使用default_tables
    ↓
为所有匹配的数据源设置表
    ↓
支持跨库查询
```

### 循环检测双重保护
```
【窗口式检测】              【连续性检测】
检测范围：最近15次          检测范围：全局连续
触发条件：≥10次            触发条件：连续≥5次相同SQL错误
适用场景：重复工具调用      适用场景：SQL执行失败
```

---

## 🎉 优化效果

### 问题修复
1. ✅ 修复Agent无法识别多数据源表映射的问题
2. ✅ 修复跨库查询时Agent陷入循环的问题
3. ✅ 修复循环检测阈值过严导致误判的问题
4. ✅ 修复SQL执行失败时循环检测失效的问题

### 功能增强
1. ✅ 支持多数据源智能路由（跨库查询）
2. ✅ 默认表始终加载（作为基础表）
3. ✅ 循环检测参数可配置化
4. ✅ 新增连续SQL错误检测机制

### 稳定性提升
1. ✅ 双重循环保护（窗口式 + 连续性）
2. ✅ 智能错误重置（成功或不同错误时）
3. ✅ 详细日志输出（便于调试）
4. ✅ 快速失败机制（避免无限循环）

---

## 📊 影响范围

### 向后兼容性
- ✅ 完全向后兼容
- ✅ 不影响现有功能
- ✅ 新增机制作为补充

### 性能影响
- ✅ 每次query_data增加少量字符串比较开销（可忽略）
- ✅ 智能表选择逻辑优化，减少无效查询

---

## 🧪 测试建议

### 测试场景
1. **跨库查询测试**: 询问涉及多个数据源的问题（如：OA会议 + HR人员）
2. **循环检测测试**: 故意构造失败的SQL查询，验证5次后是否停止
3. **默认表测试**: 无匹配关键词时，验证是否使用默认表
4. **日志验证**: 查看循环检测警告日志是否正常输出

### 预期结果
- Agent能正确识别并使用多个数据源的表
- 连续5次相同SQL错误时强制停止
- 日志输出清晰的循环检测警告信息

---

## 📝 配置说明

### 数据源配置 (`config/default_datasource.json`)
```json
{
  "auto_select_tables": true,
  "default_tables": ["base_table1", "base_table2"],
  "table_mapping": {
    "关键词1": ["表1", "表2"],
    "关键词2": ["表3", "表4"]
  }
}
```

### 循环检测参数调整
如需调整阈值，修改 `agent/agent.py` 第1858-1867行：
```python
_MAX_IDENTICAL_CALLS = 10          # 建议范围：5-15
_MAX_CONSECUTIVE_SQL_ERRORS = 5    # 建议范围：3-10
_recent_tool_window = 15           # 建议范围：10-30
```

---

## 🔗 相关文档

- 详细设计文档：`README_TABLE_SELECTION.md`
- 循环检测修复：`LOOP_DETECTION_FIX.md`
- SQL错误检测：`CONSECUTIVE_SQL_ERROR_DETECTION.md`

---

## 👥 贡献者

- 优化设计与实现：基于用户反馈和实际运行日志分析
- 测试验证：通过实际业务场景验证

---

## 📅 版本信息

- **分支**: custom-dev
- **提交时间**: 2026-08-26
- **主要变更**: Agent智能表选择与循环检测优化
- **代码行数**: +732行（agent/agent.py: +261, api/chat.py: +471）
