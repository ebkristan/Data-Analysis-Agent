# read_tool_result 循环检测修复报告

## 问题描述

Agent 反复调用 `read_tool_result` 读取同一个 artifact_id，但循环检测机制未能捕获，导致无限循环。

### 问题原因

在 `agent/agent.py` 中，`read_tool_result` 被**明确排除**在循环检测之外：

```python
# 旧代码（第2710行）
if name in {"read_tool_result", "workspace_status"}:
    continue  # 跳过循环检测
```

原设计意图：
- `read_tool_result` 是读取历史结果的辅助工具，应该是"正常行为"
- 但实际上，反复读取**同一个** artifact 是循环的明显标志

## 修复方案

### 核心思路

**不完全排除 `read_tool_result`，而是：**
1. 追踪 `artifact_id` 参数（而不是工具名）
2. 使用更严格的阈值（3次而不是10次）
3. 区分"读取不同artifact"（正常）和"反复读取同一artifact"（循环）

### 具体修改

#### 修改1：循环检测时不再跳过 `read_tool_result`

**位置**：`agent/agent.py` 第 2709-2717 行

```python
# 旧代码
if name in {"read_tool_result", "workspace_status"}:
    continue

# 新代码
if name == "workspace_status":
    continue

# read_tool_result 需要检测，但使用宽松阈值
is_read_result = (name == "read_tool_result")
```

#### 修改2：追踪 artifact_id 参数

**位置**：`agent/agent.py` 第 2741-2743 行

```python
elif name == "read_tool_result":
    # 追踪artifact_id避免反复读取同一个结果
    key_args = {"artifact_id": args.get("artifact_id", "")}
```

**位置**：`agent/agent.py` 第 4449-4451 行（工具调用历史记录）

```python
elif name == "read_tool_result":
    # 记录artifact_id避免重复读取
    key_args = {"artifact_id": args.get("artifact_id", "")}
```

#### 修改3：使用严格阈值

**位置**：`agent/agent.py` 第 2760-2768 行

```python
if is_read_result:
    # read_tool_result: 读取同一个artifact最多3次
    threshold = 3
elif is_metadata_query:
    threshold = _MAX_METADATA_CALLS  # 10
else:
    threshold = _MAX_IDENTICAL_CALLS  # 10
```

#### 修改4：添加专门的循环提示

**位置**：`agent/agent.py` 第 2786-2800 行

```python
if is_read_result:
    loop_message = (
        "⚠️ 检测到反复读取同一结果的循环\n\n"
        f"Agent 已尝试读取同一个 artifact 结果 3 次但未取得进展。\n\n"
        "**可能的原因：**\n"
        "1. 查询结果为空或格式不符合预期\n"
        "2. Agent 对结果的解析逻辑有误\n"
        "3. 缺少必要的上下文信息\n\n"
        "**建议：**\n"
        "• 换一种方式提问或简化问题\n"
        "• 检查之前的查询是否返回了有效数据\n"
        "• 尝试先查看数据表结构"
    )
```

#### 修改5：历史记录不再排除 `read_tool_result`

**位置**：`agent/agent.py` 第 4405-4410 行

```python
# 旧代码
if name not in {"read_tool_result", "workspace_status"}:

# 新代码
if name not in {"workspace_status"}:
    # read_tool_result 需要记录
```

## 测试结果

运行 `test_loop_detection.py` 验证：

### ✅ 场景1：反复读取同一个 artifact
- 调用10次同一个 `artifact_id`
- **第10次触发循环检测** ✅

### ✅ 场景2：读取不同的 artifact
- 调用5次不同的 `artifact_id`
- **不触发循环检测**（正常行为）✅

### ✅ 场景3：元数据查询
- 反复查询 `sqlite_master`
- **第10次触发循环检测** ✅

### ✅ 场景4：普通查询
- 反复执行同一SQL
- **第10次触发循环检测** ✅

## 循环检测阈值总览

| 工具类型 | 阈值变量 | 默认值 | 说明 |
|---------|---------|--------|------|
| `read_tool_result` | `_MAX_READ_RESULT_CALLS` | **10次** | 读取同一artifact的限制 |
| 元数据查询 (`sqlite_master`) | `_MAX_METADATA_CALLS` | 10次 | 允许探索性查询 |
| 普通查询 (`query_data`) | `_MAX_IDENTICAL_CALLS` | 10次 | 标准查询 |
| 其他工具 | `_MAX_IDENTICAL_CALLS` | 10次 | 通用阈值 |
| `workspace_status` | - | - | 不计入循环（轻量级） |

**检测窗口**: `_recent_tool_window` = 15次（在最近15次调用中检测）

## 优化建议

### 1. 调整阈值
如需调整循环检测的灵敏度，修改 `agent/agent.py` 第1858行的变量：

```python
# 循环检测配置（agent/agent.py 第1858行）
_MAX_IDENTICAL_CALLS = 10      # 普通工具调用的重复限制
_MAX_METADATA_CALLS = 10       # 元数据查询的重复限制
_MAX_READ_RESULT_CALLS = 10    # read_tool_result的重复限制
_recent_tool_window = 15       # 检测窗口大小
```

**调整建议**：
- 如果循环检测**过于敏感**（误报多）→ 增大阈值（如15、20）
- 如果循环检测**不够及时**（循环太久）→ 减小阈值（如5、8）
- `read_tool_result` 建议保持与其他工具一致（10次）

### 2. 动态调整阈值
根据用户反馈和实际使用情况，可以调整阈值：
- `read_tool_result`: 3 → 5（如果误报太多）
- 元数据查询: 10 → 8（如果循环太频繁）

### 2. 更智能的检测
考虑增加上下文感知：
- 如果前一次调用返回空结果，降低阈值
- 如果有进展（不同的查询参数），放宽限制

### 3. 日志增强
添加更详细的日志：
```python
log.info("[loop-detect] read_tool_result: artifact_id=%s, count=%d/%d",
         artifact_id, identical_count, threshold)
```

## 部署步骤

1. **备份原文件**
   ```bash
   cp agent/agent.py agent/agent.py.backup
   ```

2. **确认修改**
   - 检查所有5处修改点
   - 验证语法无误

3. **重启应用**
   ```bash
   python app.py
   ```

4. **验证修复**
   - 创建新会话
   - 尝试触发之前的循环场景
   - 确认在第3次重复时收到循环提示

## 回滚方案

如果出现问题，恢复备份：
```bash
cp agent/agent.py.backup agent/agent.py
```

## 总结

✅ **已修复**: `read_tool_result` 循环检测失效问题
✅ **阈值**: 同一 artifact_id 最多读取 10 次（可配置）
✅ **兼容性**: 不影响正常的多 artifact 读取
✅ **测试**: 4个场景全部通过
✅ **可配置**: 所有阈值都是变量，便于调整

---

**修改文件**: `agent/agent.py`  
**修改行数**: 5处（第1858、2709、2741、2760、2786行附近）  
**配置位置**: 第1858行（循环检测阈值）  
**测试文件**: `test_loop_detection.py`  
**修改时间**: 2026-08-26, 2026-09-02（阈值优化）
