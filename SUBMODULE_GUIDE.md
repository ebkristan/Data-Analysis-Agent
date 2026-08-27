# Git Submodule 管理指南

## 🎯 适用场景
- 你的定制内容非常多，希望独立维护
- 需要在多个项目中复用你的扩展
- 团队协作开发，需要独立的代码审查流程

---

## 🚀 设置步骤

### 1. 创建独立的扩展仓库

```bash
# 在 GitHub 创建新仓库：my-data-analysis-extensions
# 然后在本地：

cd /path/to/separate/location
mkdir my-data-analysis-extensions
cd my-data-analysis-extensions
git init

# 创建目录结构
mkdir -p custom_datasources custom_tools custom_charts
echo "# My Custom Extensions" > README.md

git add .
git commit -m "init: 初始化扩展仓库"
git branch -M main
git remote add origin https://github.com/yourusername/my-data-analysis-extensions.git
git push -u origin main
```

### 2. 在主项目中添加子模块

```bash
cd /path/to/Data-Analysis-Agent-main

# 添加扩展仓库作为子模块
git submodule add https://github.com/yourusername/my-data-analysis-extensions.git extensions

# 提交子模块配置
git add .gitmodules extensions
git commit -m "feat: 添加自定义扩展子模块"
```

### 3. 初始化和更新子模块

```bash
# 克隆主项目时初始化子模块
git clone https://github.com/Zafer-Liu/Data-Analysis-Agent.git
cd Data-Analysis-Agent
git submodule init
git submodule update

# 或一次性克隆（包含子模块）
git clone --recursive https://github.com/Zafer-Liu/Data-Analysis-Agent.git
```

---

## 🔄 日常工作流

### 开发自定义扩展

```bash
# 1. 进入子模块目录
cd extensions

# 2. 创建新分支开发
git checkout -b feature/new-tool

# 3. 进行开发
# ... 编辑文件 ...

# 4. 提交到扩展仓库
git add .
git commit -m "feat: 添加新工具"
git push origin feature/new-tool

# 5. 返回主项目，更新子模块引用
cd ..
git add extensions
git commit -m "chore: 更新扩展子模块"
git push
```

### 拉取上游更新（主项目）

```bash
# 1. 更新主项目
git fetch upstream
git merge upstream/main

# 2. 子模块不受影响，继续使用你的版本
git submodule update

# 3. 推送到你的 Fork
git push origin main
```

### 更新扩展子模块

```bash
# 在主项目中更新子模块到最新版本
git submodule update --remote extensions

# 或进入子模块手动拉取
cd extensions
git pull origin main
cd ..
git add extensions
git commit -m "chore: 更新扩展到最新版本"
```

---

## 📦 子模块结构示例

```
Data-Analysis-Agent/            (主项目)
├── .gitmodules                 (子模块配置)
├── extensions/                 (子模块 → my-data-analysis-extensions)
│   ├── .git                   (独立 Git 仓库)
│   ├── custom_datasources/
│   ├── custom_tools/
│   └── README.md
├── agent/
├── api/
└── ...
```

---

## 🎓 子模块命令速查

```bash
# 添加子模块
git submodule add <repo-url> <path>

# 初始化子模块
git submodule init

# 更新子模块
git submodule update

# 更新到远程最新
git submodule update --remote

# 查看子模块状态
git submodule status

# 删除子模块
git submodule deinit extensions
git rm extensions
rm -rf .git/modules/extensions
```

---

## ⚠️ 注意事项

1. **子模块是独立的 Git 仓库**
   - 有自己的提交历史
   - 需要单独推送
   
2. **主项目只记录子模块的提交 ID**
   - 不包含子模块的实际文件
   - 克隆时需要 `--recursive` 或手动初始化
   
3. **团队协作时需要同步**
   - 确保所有成员都初始化了子模块
   - 更新子模块后记得提交主项目

---

## 🔄 完整更新流程

```bash
# 1. 更新主项目
git checkout main
git fetch upstream
git merge upstream/main

# 2. 子模块保持独立
cd extensions
git status  # 确认你的扩展没有受影响

# 3. 如果需要，更新扩展
git pull origin main

# 4. 返回主项目并提交
cd ..
git add extensions
git commit -m "chore: 同步上游更新，保留自定义扩展"
git push origin main
```

---

## 🎯 优缺点

### 优点 ✅
- 完全隔离，上游更新不会影响扩展
- 可以独立版本控制和发布
- 团队可以单独维护扩展仓库

### 缺点 ❌
- 学习曲线较陡
- 需要管理两个仓库
- 克隆项目时需要额外步骤
