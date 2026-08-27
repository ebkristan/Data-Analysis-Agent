# 🎉 Git 配置已修正！接下来该做什么？

## ✅ 当前配置状态

你的 Git 远程仓库已经正确配置：

```
origin   → https://github.com/ebkristan/Data-Analysis-Agent.git  (你的Fork)
upstream → https://github.com/Zafer-Liu/Data-Analysis-Agent.git  (原作者)
```

---

## 📝 接下来的步骤

### 第 1 步：检查当前分支

在 PowerShell 或命令行中运行：

```powershell
git branch
```

你应该会看到当前在 `main` 或 `master` 分支上。

---

### 第 2 步：创建你的开发分支

**重要**：永远不要在 `main` 分支上直接开发！

```powershell
# 创建并切换到开发分支
git checkout -b custom-dev

# 推送到你的远程仓库
git push -u origin custom-dev
```

**解释**：
- `custom-dev` 是你的主开发分支名（可以改成你喜欢的名字）
- `-b` 表示创建新分支
- `-u origin custom-dev` 表示设置上游跟踪，以后直接 `git push` 就可以了

---

### 第 3 步：开始开发

现在你可以安心开发了！所有修改都在 `custom-dev` 分支上：

```powershell
# 修改代码...
# 例如：修改 api/chat.py，添加新功能

# 查看修改状态
git status

# 添加修改到暂存区
git add .

# 提交修改
git commit -m "feat: 添加自定义聊天功能"

# 推送到你的远程仓库
git push
```

---

### 第 4 步：定期同步上游更新

**每周或每次原作者发布新版本时执行：**

#### 方式 1：手动执行（推荐学习）

```powershell
# 1. 切换到 main 分支
git checkout main

# 2. 拉取原作者的最新更新
git fetch upstream
git merge upstream/main

# 3. 推送到你的 Fork
git push origin main

# 4. 切换回开发分支
git checkout custom-dev

# 5. 合并 main 的更新到开发分支
git merge main

# 6. 如果有冲突，解决后推送
git push
```

#### 方式 2：使用自动脚本（更快）

我已经为你创建了自动化脚本，但需要先启用 PowerShell 脚本执行权限。

**启用脚本执行权限（仅需一次）：**

以**管理员身份**运行 PowerShell，然后执行：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

选择 `Y` 确认。

然后就可以使用脚本了：

```powershell
# 在项目目录运行
.\sync-upstream.ps1

# 预览模式（不实际执行）
.\sync-upstream.ps1 -DryRun

# 创建备份
.\sync-upstream.ps1 -Backup
```

---

## 🔧 处理合并冲突

如果在合并时遇到冲突，不要慌张！

### 步骤 1：查看冲突文件

```powershell
git status
```

会显示类似：

```
both modified:   api/chat.py
both modified:   agent/agent.py
```

### 步骤 2：打开冲突文件

用编辑器打开 `api/chat.py`，你会看到：

```python
<<<<<<< HEAD
# 你的代码
def my_custom_function():
    pass
=======
# 上游代码
def original_function():
    pass
>>>>>>> upstream/main
```

### 步骤 3：手动编辑，保留需要的内容

删除 `<<<<<<<`、`=======`、`>>>>>>>` 标记，保留你需要的代码：

```python
# 保留你的代码
def my_custom_function():
    pass

# 同时保留上游新增的功能
def original_function():
    pass
```

### 步骤 4：标记为已解决

```powershell
git add api/chat.py
git add agent/agent.py
```

### 步骤 5：完成合并

```powershell
git commit -m "merge: 解决冲突，合并上游更新"
git push
```

---

## 📋 推荐的工作流程

### 日常开发流程

```
1. 早上打开项目
   ↓
2. git pull  （拉取团队成员的更新，如果有）
   ↓
3. 开发新功能
   ↓
4. git add . && git commit -m "feat: 新功能"
   ↓
5. git push
   ↓
6. 继续开发...
```

### 每周同步上游

```
1. 周一或原作者发布新版本时
   ↓
2. git checkout main
   ↓
3. git fetch upstream && git merge upstream/main
   ↓
4. git push origin main
   ↓
5. git checkout custom-dev
   ↓
6. git merge main
   ↓
7. 解决冲突（如果有）
   ↓
8. git push
```

---

## 🎯 开发建议

### ✅ 推荐的修改方式

#### 1. **扩展而非修改**

创建新文件，而不是直接修改原有文件：

```
❌ 不推荐：直接修改 agent/agent.py
✅ 推荐：创建 extensions/my_custom_agent.py
```

#### 2. **使用配置覆盖**

```python
# 在你的代码中
from agent.agent import BusinessAgent

class MyCustomAgent(BusinessAgent):
    """继承并扩展，而不是修改原类"""
    
    def custom_method(self):
        # 你的自定义逻辑
        pass
```

#### 3. **标记你的修改**

如果必须修改原文件，用明显的注释标记：

```python
# === CUSTOM MODIFICATION START - by ebkristan ===
# 自定义功能：添加情感分析
def sentiment_analysis():
    pass
# === CUSTOM MODIFICATION END ===
```

这样更新时容易识别和保留。

---

## 📁 推荐的项目结构

```
Data-Analysis-Agent-main/
├── main (分支)                  ← 只用于同步上游，不开发
├── custom-dev (分支)            ← 你的主开发分支
│
├── extensions/                  ← 你的自定义扩展（新建）
│   ├── __init__.py
│   ├── custom_tools/
│   ├── custom_datasources/
│   └── custom_apis/
│
├── config/custom/               ← 你的自定义配置（新建）
│   ├── settings.py
│   └── datasources.json
│
└── .env.custom                  ← 你的环境变量（新建）
```

---

## 🆘 常见问题

### Q: 如何查看我修改了哪些文件？

```powershell
git status              # 查看当前修改
git diff                # 查看具体改动
git log --oneline -10   # 查看最近10次提交
```

### Q: 如何撤销一个错误的提交？

```powershell
# 撤销最后一次提交（保留修改）
git reset --soft HEAD~1

# 撤销最后一次提交（丢弃修改，危险！）
git reset --hard HEAD~1
```

### Q: 如何暂时保存当前修改？

```powershell
# 暂存当前修改
git stash

# 做其他事情...
git checkout main
git pull

# 恢复之前的修改
git checkout custom-dev
git stash pop
```

### Q: 如何查看上游有哪些新功能？

```powershell
# 查看上游更新日志
git fetch upstream
git log main..upstream/main --oneline

# 查看具体改动
git diff main..upstream/main
```

### Q: 推送时提示冲突怎么办？

```powershell
# 先拉取远程更新
git pull

# 解决冲突后再推送
git push
```

---

## 📚 下一步学习资源

1. **Git 工作流完整指南**：`.git-workflow-guide.md`
2. **自定义扩展开发**：`CUSTOM_EXTENSION_GUIDE.md`
3. **快速参考卡**：`QUICK_REFERENCE.md`

---

## 🎓 Git 命令速查表

```powershell
# 分支操作
git branch                    # 查看本地分支
git branch -r                 # 查看远程分支
git checkout 分支名           # 切换分支
git checkout -b 新分支        # 创建并切换分支

# 提交操作
git add .                     # 添加所有修改
git commit -m "消息"          # 提交
git push                      # 推送到远程

# 同步操作
git fetch upstream            # 获取上游更新
git pull                      # 拉取并合并
git merge 分支名              # 合并分支

# 查看操作
git status                    # 查看状态
git log                       # 查看历史
git diff                      # 查看改动

# 撤销操作
git reset --soft HEAD~1       # 撤销提交（保留修改）
git reset --hard HEAD~1       # 撤销提交（丢弃修改）
git checkout -- 文件          # 撤销文件修改
```

---

## ✅ 检查清单

配置完成后，检查这些项目：

- [ ] 远程仓库配置正确（origin = 你的Fork，upstream = 原作者）
- [ ] 已创建 `custom-dev` 开发分支
- [ ] 已推送开发分支到远程
- [ ] 理解了日常开发流程
- [ ] 知道如何同步上游更新
- [ ] 知道如何处理冲突

---

**🎉 恭喜！你现在可以安全地进行二次开发了！**

有任何问题，随时查看这些文档或提问。
