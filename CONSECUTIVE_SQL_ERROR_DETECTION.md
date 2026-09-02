# 连续SQL错误检测机制

## 问题背景

### 发现的问题
在实际运行中发现，agent执行了20+次相同的失败SQL查询，但循环检测机制未能触发停止。

### 根本原因
1. **envelope.ok为True**：SQL执行失败时，`query_data`工具调用本身是成功的（`envelope.ok=True`），只是SQL执行结果包含错误信息
2. **窗口内穿插**：15次检测窗口内穿插了其他工具调用（如`get_table_detail`, `get_schema`等），导致同一SQL的重复次数不足10次阈值
3. **缺少专门检测**：虽然定义了`_consecutive_sql_errors`变量，但未实现检测逻辑

### 日志证据
```log
[tool] query_data args={'sql': 'SELECT state_id FROM person_state WHERE person_id = 81'}
[tool] query_data OK 0.45s result="SQL Error: Execution failed..."
```
此类日志重复出现20+次，但未触发循环停止。

## 解决方案

### 核心思路
实现**连续SQL错误检测**机制，专门针对相同SQL的连续失败进行检测和拦截。

### 实现位置
`agent/agent.py`

### 关键变量（第1858行）
```python
_consecutive_sql_errors = 0  # 连续SQL错误计数
_MAX_CONSECUTIVE_SQL_ERRORS = 5  # 最多允许5次连续SQL错误
_last_sql_error = None  # 上次错误的SQL（标准化后）
```

### 检测逻辑（第4441行之后）
在`if envelope.ok:`块中添加：

```python
# 检查是否是SQL错误（即使envelope.ok=True）
is_sql_error = False
if name == "query_data":
    result_str = str(envelope.data)
    if "SQL Error" in result_str or "Execution failed" in result_str:
        is_sql_error = True
        sql = args.get("sql", "")
        # 标准化SQL用于比较
        sql_normalized = re.sub(r'--.*', '', sql)
        sql_normalized = re.sub(r'/\*.*?\*/', '', sql_normalized, flags=re.DOTALL)
        sql_normalized = " ".join(sql_normalized.upper().split())
        
        # 检查是否与上次错误相同
        if _last_sql_error == sql_normalized:
            _consecutive_sql_errors += 1
            log.warning(
                "[sql-error-detect] consecutive SQL error #%d (max=%d): %s",
                _consecutive_sql_errors, _MAX_CONSECUTIVE_SQL_ERRORS, sql[:100]
            )
        else:
            _consecutive_sql_errors = 1
            _last_sql_error = sql_normalized
            log.info("[sql-error-detect] new SQL error detected: %s", sql[:100])
        
        # 如果达到阈值，强制停止
        if _consecutive_sql_errors >= _MAX_CONSECUTIVE_SQL_ERRORS:
            log.error(
                "[sql-error-detect] %d consecutive identical SQL errors, forcing stop",
                _consecutive_sql_errors
            )
            yield {
                "type": "text",
                "content": f"检测到连续{_consecutive_sql_errors}次相同的SQL错误，已强制停止。\n\n"
                           f"错误的SQL: {sql[:200]}\n\n"
                           f"可能的原因：\n"
                           f"1. 表或字段不存在\n"
                           f"2. 数据源配置错误\n"
                           f"3. SQL语法错误\n\n"
                           f"请检查数据源配置和表映射设置。"
            }
            yield {"type": "done"}
            return
    else:
        # SQL执行成功，重置错误计数
        if _consecutive_sql_errors > 0:
            log.info("[sql-error-detect] SQL execution successful, resetting error count")
        _consecutive_sql_errors = 0
        _last_sql_error = None
```

### 关键特性

1. **独立于循环检测**
   - 不依赖检测窗口大小
   - 只关注**连续**相同的SQL错误
   - 与现有循环检测机制互补

2. **SQL标准化**
   - 移除注释（单行和多行）
   - 统一大小写和空白
   - 确保相同逻辑的SQL被识别为相同

3. **智能重置**
   - SQL执行成功时自动重置计数
   - 检测到不同SQL错误时重置计数
   - 只有**连续相同**的SQL错误才累加

4. **不污染循环检测**
   ```python
   # 只有非SQL错误的调用才计入历史（避免错误SQL污染循环检测）
   if not is_sql_error:
       _tool_call_history.append(call_signature)
   ```

### 迭代轮次检查（第1883行）
添加在每次迭代开始时的检查：
```python
if _consecutive_sql_errors >= _MAX_CONSECUTIVE_SQL_ERRORS:
    log.error("[run] %d consecutive identical SQL errors, aborting", _consecutive_sql_errors)
    yield {
        "type": "text", 
        "content": f"检测到连续{_consecutive_sql_errors}次相同的SQL错误，已强制停止。请检查数据源配置和SQL语法。"
    }
    yield {"type": "done"}
    return
```

## 与现有机制的关系

### 现有循环检测（第2709行）
- **检测范围**：最近15次工具调用
- **触发条件**：相同调用≥10次
- **局限性**：窗口内穿插其他调用时可能失效

### 新增SQL错误检测
- **检测范围**：全局连续性
- **触发条件**：相同SQL错误连续≥5次
- **优势**：不受其他工具调用干扰

### 互补关系
```
循环检测      → 检测重复工具调用模式（窗口内）
SQL错误检测   → 检测连续SQL执行失败（全局）
```

## 配置参数

### 可调整阈值
```python
_MAX_CONSECUTIVE_SQL_ERRORS = 5  # 建议值：3-10
```

### 建议值
- **3次**：严格模式，快速失败
- **5次**：平衡模式（当前值），给agent一定重试空间
- **10次**：宽松模式，适用于探索性场景

## 测试验证

### 预期行为
1. 第1-4次相同SQL错误：记录警告日志
2. 第5次相同SQL错误：触发强制停止
3. 中间有其他SQL错误：重置计数
4. SQL执行成功：重置计数

### 日志输出
```log
[sql-error-detect] consecutive SQL error #1 (max=5): SELECT state_id FROM person_state...
[sql-error-detect] consecutive SQL error #2 (max=5): SELECT state_id FROM person_state...
[sql-error-detect] consecutive SQL error #3 (max=5): SELECT state_id FROM person_state...
[sql-error-detect] consecutive SQL error #4 (max=5): SELECT state_id FROM person_state...
[sql-error-detect] consecutive SQL error #5 (max=5): SELECT state_id FROM person_state...
[sql-error-detect] 5 consecutive identical SQL errors, forcing stop
```

## 影响范围

### 修改文件
- `agent/agent.py`（3处修改）

### 兼容性
- 向后兼容，不影响现有功能
- 与现有循环检测机制互补

### 性能影响
- 每次`query_data`调用增加少量字符串比较开销
- 影响可忽略不计

## 总结

通过实现**连续SQL错误检测机制**，解决了agent在SQL执行失败时无法及时停止的问题。该机制：

1. ✅ **专门检测SQL错误**：不受其他工具调用干扰
2. ✅ **快速失败**：5次连续相同错误即停止
3. ✅ **智能重置**：成功执行或不同错误时自动重置
4. ✅ **详细反馈**：提供错误SQL和可能原因
5. ✅ **不污染循环检测**：错误的SQL不计入循环检测历史

该机制与现有的循环检测机制（基于窗口的重复调用检测）互补，形成双重保护。
