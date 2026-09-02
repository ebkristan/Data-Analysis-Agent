# Agent智能表选择机制说明

## 1. 总体流程

```
用户提问 
  ↓
智能表选择 (_smart_select_tables_from_question)
  ↓
应用分析上下文 (_apply_sql_analysis_context)
  ↓
获取数据源快照 (acquire_data_source_snapshot)
  ↓
构建Agent (_build_agent)
  ↓
Agent执行 (agent.run)
  ↓
调用工具 (get_schema, query_data)
```

## 2. 智能表选择阶段

**文件**: `api/chat.py` 第674行  
**函数**: `_smart_select_tables_from_question(sess, question)`

### 2.1 配置加载
```python
for each datasource:
    if not hasattr(source, "_auto_select_enabled"):
        _try_load_auto_select_config(source)
```

### 2.2 关键词匹配
```python
for pattern, tables in table_mapping.items():
    if re.search(pattern, question, re.IGNORECASE):
        matched_patterns.append(pattern)
        matched_tables.update(tables)
```

### 2.3 计算分数
```python
score = len(matched_patterns)  # 匹配的模式数量
```

### 2.4 选择数据源
```python
matched_sources = [c for c in candidates if c["score"] > 0]
# 注意：无论是否匹配，所有数据源都会被处理
```

### 2.5 设置表（默认表 + 匹配表）
```python
for candidate in all_candidates:
    default_tables = source._default_tables
    
    if candidate in matched_sources:
        # 有匹配：默认表 + 匹配表
        tables = set(default_tables) | matched_tables
    else:
        # 无匹配：只用默认表
        tables = set(default_tables)
    
    source.set_analysis_tables(tables)
```

## 3. 配置文件

**位置**: `config/default_datasource.json`

```json
{
  "datasources": [
    {
      "enabled": true,
      "display_name": "公共数据库",
      "auto_select_tables": true,
      "table_mapping": {
        "会议|会议室": ["meeting", "meeting_room"],
        "员工|人员": ["user", "person"]
      },
      "default_tables": ["org", "user"]
    }
  ]
}
```

## 4. 关键参数

| 参数 | 说明 |
|------|------|
| `auto_select_tables` | 启用智能表选择 |
| `table_mapping` | 关键词→表的映射 |
| `default_tables` | **始终加载的基础表** |
| `select_all_tables` | 选择所有表 |
| `max_tables` | 最多表数量 |

## 5. 完整流程示例

**用户问题**: "查询最近的10场会议"

### 步骤1: 智能选择
- OA库匹配 "会议" → score=1 → 设置 `default_tables + ["meeting", "meeting_room"]` = `["org", "user", "meeting", "meeting_room"]`
- HR库无匹配 → score=0 → 设置 `default_tables` = `["person", "position", "post"]`

### 步骤2: 生成Schema
```
数据源: 公共数据库
Table: org
  org_id INTEGER
  org_name VARCHAR

Table: user
  user_id INTEGER
  user_name VARCHAR

Table: meeting
  meeting_id INTEGER
  title VARCHAR
  start_time DATETIME

数据源: 人事数据库
Table: person
  person_id INTEGER
  person_name VARCHAR
```

### 步骤3: Agent执行
- 调用 `get_schema()` 看到 meeting 表
- 调用 `query_data("SELECT * FROM meeting LIMIT 10")`
- 自动路由到OA库

## 6. 调试方法

### 查看智能选择日志
```bash
grep "SMART-SELECT" outputs/Log/baa_*.log | tail -50
```

### 查看最终表设置
```bash
grep "Final: source=" outputs/Log/baa_*.log
```

### 检查循环
```bash
grep "loop-detect" outputs/Log/baa_*.log
```

## 7. 常见问题

### Q1: 为什么没有匹配到表？
A: 检查 `table_mapping` 中是否有相关关键词。

### Q2: 如何支持跨库查询？
A: 多个数据源同时匹配关键词即可。

### Q3: 为什么进入循环？
A: 
- 检查循环检测日志
- 优化 `table_mapping` 规则
- 确保表被正确设置

## 8. 修改记录

- 2026-08-26: 添加智能表选择机制
- 2026-08-26: 修复循环检测（read_tool_result阈值=3）
- 2026-09-02: 修复配置加载（智能选择时自动加载）
- 2026-09-02: 支持多数据源匹配（跨库查询）
- 2026-09-02: **优化表选择策略（默认表始终作为基础表）**
