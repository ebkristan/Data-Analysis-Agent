# 🚀 二次开发快速参考卡

## 📋 三种方案对比

| 方案 | 难度 | 适用场景 | 冲突风险 | 推荐度 |
|------|------|----------|----------|--------|
| **Fork + 分支** | ⭐⭐ | 中小型定制，需要提交到 Git | 低 | ⭐⭐⭐⭐⭐ |
| **配置分离** | ⭐ | 轻量定制，不改核心代码 | 极低 | ⭐⭐⭐⭐ |
| **Git Submodule** | ⭐⭐⭐⭐ | 大型扩展，独立维护 | 无 | ⭐⭐⭐ |

---

## 🎯 方案一：Fork + 分支管理（最推荐）

### 快速开始
```bash
# 1. 设置远程仓库
git remote rename origin upstream
git remote add origin https://github.com/你的用户名/Data-Analysis-Agent.git

# 2. 创建开发分支
git checkout -b custom-dev
git push -u origin custom-dev

# 3. 日常开发
# 在 custom-dev 分支上修改代码...
git add .
git commit -m "feat: 你的修改"
git push origin custom-dev
```

### 拉取上游更新
```bash
# 方式 1：使用自动脚本（推荐）
.\sync-upstream.ps1          # Windows
./sync-upstream.sh           # Linux/macOS

# 方式 2：手动执行
git checkout main
git fetch upstream
git merge upstream/main
git push origin main
git checkout custom-dev
git merge main
```

### 处理冲突
```bash
# 1. 查看冲突文件
git status

# 2. 编辑冲突文件，保留需要的内容
# 删除 <<<<<<< ======= >>>>>>> 标记

# 3. 标记为已解决
git add 冲突文件.py
git commit -m "merge: 解决冲突"
git push
```

---

## 🎯 方案二：配置与代码分离（最简单）

### 目录结构
```
Data-Analysis-Agent/
├── extensions/           ← 你的自定义代码（不会被覆盖）
│   ├── custom_datasources/
│   ├── custom_tools/
│   └── custom_apis/
├── config/custom/        ← 你的配置文件
└── .env.custom          ← 你的环境变量
```

### 修改 app.py（一次性）
在 `app.py` 末尾添加：
```python
# === CUSTOM EXTENSION START ===
def load_custom_extensions():
    import importlib
    import sys
    from pathlib import Path
    
    ext_dir = Path(__file__).parent / "extensions"
    if ext_dir.exists():
        sys.path.insert(0, str(ext_dir))
        for module_dir in ext_dir.iterdir():
            if module_dir.is_dir() and (module_dir / "__init__.py").exists():
                try:
                    importlib.import_module(module_dir.name)
                    log.info(f"[app] Loaded: {module_dir.name}")
                except Exception as e:
                    log.error(f"[app] Failed: {e}")

if not is_vercel:
    load_custom_extensions()
# === CUSTOM EXTENSION END ===
```

### 拉取更新
```bash
# 1. 备份扩展（可选）
cp -r extensions extensions_backup

# 2. 拉取更新
git pull

# 3. 检查 app.py 中的标记是否还在
# 如果被覆盖，重新添加上面的代码
```

---

## 🎯 方案三：Git Submodule（高级用户）

### 初始设置
```bash
# 1. 创建扩展仓库
mkdir my-extensions
cd my-extensions
git init
git remote add origin https://github.com/你的用户名/my-extensions.git

# 2. 在主项目中添加子模块
cd ../Data-Analysis-Agent
git submodule add https://github.com/你的用户名/my-extensions.git extensions
```

### 拉取更新
```bash
# 更新主项目（子模块不受影响）
git pull upstream main

# 子模块继续使用你的版本
git submodule update
```

---

## 🛠️ 常用命令速查

### Git 分支操作
```bash
git branch                    # 查看分支
git checkout -b 分支名         # 创建并切换分支
git merge 分支名              # 合并分支
git branch -d 分支名          # 删除分支
```

### 冲突解决
```bash
git status                    # 查看冲突文件
git checkout --ours 文件      # 使用你的版本
git checkout --theirs 文件    # 使用上游版本
git merge --abort             # 取消合并
```

### 回滚操作
```bash
git log --oneline             # 查看提交历史
git reset --hard <commit-id>  # 回滚到指定提交
git reset --hard HEAD~1       # 回滚到上一次提交
```

### 暂存修改
```bash
git stash                     # 暂存当前修改
git stash pop                 # 恢复暂存
git stash list                # 查看暂存列表
```

---

## 📁 推荐的文件组织

### 不要修改的文件（直接更新）
```
agent/agent.py               # Agent 核心引擎
agent/tools/business/        # 业务工具
api/chat.py                  # 聊天接口
data/sources/                # 数据源实现
```

### 可以修改的文件（标记修改）
```
app.py                       # 启动入口（添加扩展加载）
api/__init__.py             # API 注册（添加自定义路由）
```

### 你的自定义文件（永远不冲突）
```
extensions/                  # 你的扩展代码
config/custom/              # 你的配置
.env.custom                 # 你的环境变量
*.custom.py                 # 自定义 Python 文件
```

---

## ⚠️ 注意事项

### ✅ 推荐做法
- 定期备份（每次大改前）
- 使用明确的提交信息
- 保持提交粒度小
- 及时同步上游更新
- 用注释标记你的修改

### ❌ 避免做法
- 直接在 main 分支开发
- 修改大量核心文件
- 长时间不同步上游
- 强制推送 (`--force`) 到共享分支
- 忽略冲突提示

---

## 🆘 常见问题

### Q: 更新后项目启动失败？
```bash
# 1. 检查依赖是否更新
pip install -r requirements.txt --upgrade

# 2. 清除缓存
rm -rf __pycache__
find . -name "*.pyc" -delete

# 3. 检查配置文件
cat .env
```

### Q: 合并冲突太多怎么办？
```bash
# 取消当前合并
git merge --abort

# 使用 rebase 替代（更清晰）
git rebase upstream/main

# 或者只拉取特定文件
git checkout upstream/main -- 特定文件.py
```

### Q: 如何查看上游有哪些更新？
```bash
# 查看上游提交历史
git log upstream/main --oneline -10

# 查看具体改动
git diff main..upstream/main

# 查看改动的文件列表
git diff --name-only main..upstream/main
```

---

## 📚 相关文档

- 📖 [Git 工作流完整指南](.git-workflow-guide.md)
- 🔧 [自定义扩展开发指南](CUSTOM_EXTENSION_GUIDE.md)
- 📦 [Git Submodule 管理](SUBMODULE_GUIDE.md)

---

## 🎓 推荐学习资源

- Git 可视化学习：https://learngitbranching.js.org/
- Git 官方文档：https://git-scm.com/doc
- GitHub 工作流：https://docs.github.com/en/get-started/quickstart/github-flow

---

**记住：备份是最好的保险！**

每次大改前执行：
```bash
git branch backup-$(date +%Y%m%d)
git push origin backup-$(date +%Y%m%d)
```
