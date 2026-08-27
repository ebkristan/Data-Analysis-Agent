# 自定义扩展开发指南

## 🎯 目标
通过模块化设计，让你的定制内容与上游代码解耦，更新时零冲突。

---

## 📁 推荐的目录结构

```
Data-Analysis-Agent/
├── extensions/              # 【新建】你的扩展目录（不会被覆盖）
│   ├── __init__.py
│   ├── custom_datasources/  # 自定义数据源
│   ├── custom_tools/         # 自定义工具
│   ├── custom_charts/        # 自定义图表
│   ├── custom_apis/          # 自定义API接口
│   └── custom_ui/            # 自定义UI组件
│
├── config/
│   ├── config.py            # 【不修改】原始配置
│   └── custom/              # 【新建】你的自定义配置
│       ├── __init__.py
│       ├── datasources.json
│       ├── llm.json
│       └── ui_theme.json
│
├── .env                     # 【不修改】原始环境变量
├── .env.custom              # 【新建】你的环境变量
│
└── app.py                   # 【修改】添加扩展加载逻辑
```

---

## 🔧 实现步骤

### 1. 创建扩展目录

在项目根目录创建 `extensions` 文件夹：

```bash
mkdir extensions
mkdir extensions/custom_datasources
mkdir extensions/custom_tools
mkdir extensions/custom_charts
mkdir extensions/custom_apis
mkdir extensions/custom_ui
```

### 2. 修改 app.py 加载扩展

在 `app.py` 中添加扩展加载逻辑：

```python
# app.py 末尾添加
# -------------------------------
# 加载自定义扩展
# -------------------------------
def load_custom_extensions():
    """动态加载 extensions 目录下的扩展"""
    import importlib
    import sys
    from pathlib import Path
    
    ext_dir = Path(__file__).parent / "extensions"
    if not ext_dir.exists():
        log.info("[app] No custom extensions directory found")
        return
    
    # 将扩展目录添加到 Python 路径
    sys.path.insert(0, str(ext_dir))
    
    # 自动加载所有扩展模块
    for module_dir in ext_dir.iterdir():
        if module_dir.is_dir() and (module_dir / "__init__.py").exists():
            try:
                module_name = module_dir.name
                importlib.import_module(module_name)
                log.info(f"[app] Loaded custom extension: {module_name}")
            except Exception as e:
                log.error(f"[app] Failed to load extension {module_dir.name}: {e}")

# 在创建 app 之后加载扩展
if not is_vercel:
    load_custom_extensions()
```

### 3. 自定义数据源示例

创建 `extensions/custom_datasources/mongodb_source.py`：

```python
# extensions/custom_datasources/mongodb_source.py
from data.datasource import DataSource
import pymongo

class MongoDBDataSource(DataSource):
    """自定义 MongoDB 数据源"""
    
    def __init__(self, connection_string: str, database: str):
        super().__init__()
        self.client = pymongo.MongoClient(connection_string)
        self.db = self.client[database]
    
    def get_schema(self) -> str:
        """返回 MongoDB 集合结构"""
        collections = self.db.list_collection_names()
        schema = f"MongoDB Database: {self.db.name}\n\n"
        
        for coll_name in collections:
            coll = self.db[coll_name]
            sample = coll.find_one()
            if sample:
                fields = list(sample.keys())
                schema += f"Collection: {coll_name}\n"
                schema += f"Fields: {', '.join(fields)}\n\n"
        
        return schema
    
    def execute_query(self, query: dict):
        """执行 MongoDB 查询"""
        # 实现查询逻辑
        pass

# 注册到系统
from data.registry import register_datasource
register_datasource("mongodb", MongoDBDataSource)
```

### 4. 自定义工具示例

创建 `extensions/custom_tools/sentiment_analysis.py`：

```python
# extensions/custom_tools/sentiment_analysis.py

def sentiment_analysis_tool():
    """情感分析工具"""
    return {
        "type": "function",
        "function": {
            "name": "analyze_sentiment",
            "description": "对文本数据进行情感分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "text_column": {
                        "type": "string",
                        "description": "包含文本的列名"
                    }
                },
                "required": ["text_column"]
            }
        }
    }

def analyze_sentiment(text_column: str, data_source):
    """实现情感分析逻辑"""
    # 你的实现
    pass

# 注册工具
from agent.tools.registry import register_tool
register_tool("analyze_sentiment", sentiment_analysis_tool, analyze_sentiment)
```

### 5. 自定义 API 接口

创建 `extensions/custom_apis/custom_endpoints.py`：

```python
# extensions/custom_apis/custom_endpoints.py
from flask import Blueprint, request, jsonify

custom_bp = Blueprint('custom_api', __name__, url_prefix='/api/custom')

@custom_bp.route('/my-endpoint', methods=['POST'])
def my_custom_endpoint():
    """你的自定义接口"""
    data = request.json
    # 处理逻辑
    return jsonify({"status": "success"})

# 在 api/__init__.py 中注册
# from extensions.custom_apis.custom_endpoints import custom_bp
# app.register_blueprint(custom_bp)
```

### 6. 自定义配置管理

创建 `config/custom/settings.py`：

```python
# config/custom/settings.py

# 自定义数据源配置
CUSTOM_DATASOURCES = {
    "mongodb": {
        "enabled": True,
        "connection_string": "mongodb://localhost:27017",
        "database": "analytics"
    }
}

# 自定义 UI 配置
CUSTOM_UI_THEME = {
    "primary_color": "#1890ff",
    "logo_path": "/custom/logo.png"
}

# 自定义功能开关
FEATURE_FLAGS = {
    "enable_sentiment_analysis": True,
    "enable_custom_charts": True
}
```

### 7. 环境变量隔离

创建 `.env.custom` 文件：

```bash
# .env.custom - 你的私有配置

# 自定义 API Keys
CUSTOM_API_KEY=your_secret_key

# 自定义数据库
CUSTOM_DB_HOST=localhost
CUSTOM_DB_PORT=5432

# 自定义功能开关
ENABLE_CUSTOM_FEATURES=true
```

在 `app.py` 中加载：

```python
from dotenv import load_dotenv
load_dotenv()  # 加载 .env
load_dotenv('.env.custom', override=True)  # 加载自定义配置，覆盖默认值
```

---

## 🔄 更新流程

### 当上游更新时：

```bash
# 1. 备份你的扩展
cp -r extensions extensions_backup

# 2. 拉取上游更新
git pull upstream main

# 3. 如果 extensions 目录被覆盖，恢复备份
cp -r extensions_backup extensions

# 4. 检查是否有配置文件冲突
git status

# 5. 重新加载自定义扩展
python app.py
```

### 推荐：将扩展纳入版本控制

```bash
# 只跟踪你的扩展目录
git add extensions/
git add config/custom/
git add .env.custom
git commit -m "feat: 添加自定义扩展"

# 推送到你的分支
git push origin custom-dev
```

---

## 📋 修改文件清单

### 需要修改的原始文件（标记你的修改）：

```python
# app.py
# === CUSTOM EXTENSION START ===
load_custom_extensions()
# === CUSTOM EXTENSION END ===

# api/__init__.py
# === CUSTOM API START ===
from extensions.custom_apis.custom_endpoints import custom_bp
app.register_blueprint(custom_bp)
# === CUSTOM API END ===
```

**好处**：用明显的注释标记，更新时容易识别和保留。

---

## 🎯 最佳实践

1. **所有自定义代码放在 `extensions/` 目录**
2. **所有自定义配置放在 `config/custom/` 或 `.env.custom`**
3. **用注释标记对原始文件的修改**
4. **定期备份扩展目录**
5. **使用版本控制管理你的扩展**

---

## 🔍 冲突检测

更新前运行检测脚本：

```bash
# 创建 check_conflicts.sh
#!/bin/bash

echo "检查可能冲突的文件..."

# 检查是否有未跟踪的自定义文件
git status --porcelain | grep "^??" | grep -v "extensions/" | grep -v "config/custom/"

# 检查是否有修改的核心文件
git diff --name-only

echo "检查完成！"
```

---

## 📦 打包你的扩展

如果要分享你的扩展：

```bash
# 打包扩展
tar -czf my-custom-extensions.tar.gz extensions/ config/custom/ .env.custom

# 或使用 Git 子模块
git submodule add https://github.com/yourusername/custom-extensions.git extensions
```

---

## 🆘 遇到问题？

### 扩展加载失败
- 检查 `extensions/__init__.py` 是否存在
- 查看日志输出：`LOG_DIR/app.log`

### 配置不生效
- 确认 `.env.custom` 在 `.env` 之后加载
- 使用 `os.getenv()` 验证环境变量

### API 接口 404
- 确认 Blueprint 已注册
- 检查 URL 前缀是否正确
