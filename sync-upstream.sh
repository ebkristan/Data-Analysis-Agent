#!/bin/bash
# ====================================
# 上游同步脚本 - Linux/macOS
# ====================================
# 用途：安全地拉取上游更新，同时保留你的自定义修�?
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

# 参数解析
DRY_RUN=false
BACKUP=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --backup)
            BACKUP=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        *)
            echo -e "${RED}[错误] 未知参数: $1${NC}"
            echo "用法: $0 [--dry-run] [--backup] [--force]"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}  上游同步工具${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

# 检�?Git 仓库
if [ ! -d ".git" ]; then
    echo -e "${RED}[错误] 当前目录不是 Git 仓库�?{NC}"
    exit 1
fi

# 检查未提交的修�?if [ -n "$(git status --porcelain)" ] && [ "$FORCE" = false ]; then
    echo -e "${YELLOW}[警告] 存在未提交的修改�?{NC}"
    echo ""
    git status --porcelain
    echo ""
    echo -e "${YELLOW}请先提交或暂存修改，或使�?--force 参数强制执行${NC}"
    exit 1
fi

# 创建备份
if [ "$BACKUP" = true ]; then
    timestamp=$(date +"%Y%m%d_%H%M%S")
    backup_branch="backup-$timestamp"
    
    echo -e "${GREEN}[备份] 创建备份分支: $backup_branch${NC}"
    
    if [ "$DRY_RUN" = false ]; then
        git branch "$backup_branch"
        git push origin "$backup_branch"
    fi
fi

# 检查远程仓库配�?echo -e "${CYAN}[检查] 验证远程仓库配置...${NC}"

if ! git remote -v | grep -q "upstream"; then
    echo -e "${YELLOW}[警告] 未配�?upstream 远程仓库${NC}"
    echo ""
    read -p "是否现在配置�?y/n) " confirm
    
    if [ "$confirm" = "y" ]; then
        echo -e "${CYAN}[配置] 添加上游仓库...${NC}"
        
        if [ "$DRY_RUN" = false ]; then
            git remote add upstream https://github.com/Zafer-Liu/Data-Analysis-Agent.git
            echo -e "${GREEN}[完成] upstream 已配�?{NC}"
        fi
    else
        echo -e "${YELLOW}[取消] 用户取消操作${NC}"
        exit 0
    fi
fi

# 获取当前分支
current_branch=$(git branch --show-current)

echo -e "${CYAN}[信息] 当前分支: $current_branch${NC}"
echo ""

# 拉取上游更新
echo -e "${CYAN}[同步] 正在从上游拉取更�?..${NC}"

if [ "$DRY_RUN" = false ]; then
    # 获取上游更新
    git fetch upstream
    
    # 切换到主分支（如果不在）
    if [ "$current_branch" != "main" ]; then
        echo -e "${CYAN}[切换] 切换�?main 分支...${NC}"
        git checkout main
    fi
    
    # 合并上游更新
    echo -e "${CYAN}[合并] 合并上游 main 分支...${NC}"
    if git merge upstream/main --no-edit; then
        # 推送到你的远程
        echo -e "${CYAN}[推送] 推送到你的远程仓库...${NC}"
        git push origin main
        
        # 切换回开发分�?        if [ "$current_branch" != "main" ]; then
            echo -e "${CYAN}[切换] 切换�?$current_branch 分支...${NC}"
            git checkout "$current_branch"
            
            echo -e "${CYAN}[合并] 合并 main �?$current_branch...${NC}"
            git merge main --no-edit
        fi
        
        echo ""
        echo -e "${GREEN}[成功] 上游更新已同步！${NC}"
    else
        echo ""
        echo -e "${RED}[错误] 合并失败，存在冲�?{NC}"
        echo ""
        echo -e "${YELLOW}可能的原因：${NC}"
        echo -e "${YELLOW}  1. 存在合并冲突，需要手动解�?{NC}"
        echo -e "${YELLOW}  2. 网络连接问题${NC}"
        echo ""
        echo -e "${CYAN}解决方案�?{NC}"
        echo -e "${CYAN}  - 查看冲突文件：git status${NC}"
        echo -e "${CYAN}  - 解决冲突后：git add . && git commit${NC}"
        echo -e "${CYAN}  - 继续合并：git merge --continue${NC}"
        
        exit 1
    fi
else
    echo -e "${YELLOW}[预览模式] 将执行以下操作：${NC}"
    echo -e "${GRAY}  1. git fetch upstream${NC}"
    echo -e "${GRAY}  2. git checkout main${NC}"
    echo -e "${GRAY}  3. git merge upstream/main${NC}"
    echo -e "${GRAY}  4. git push origin main${NC}"
    echo -e "${GRAY}  5. git checkout $current_branch${NC}"
    echo -e "${GRAY}  6. git merge main${NC}"
fi

# 检查自定义文件
echo ""
echo -e "${CYAN}[检查] 扫描自定义文�?..${NC}"

custom_paths=("extensions" "config/custom" ".env.custom")
found_custom=false

for path in "${custom_paths[@]}"; do
    if [ -e "$path" ]; then
        echo -e "${GREEN}  �?发现自定义内�? $path${NC}"
        found_custom=true
    fi
done

if [ "$found_custom" = false ]; then
    echo -e "${GRAY}  �?未发现自定义扩展目录${NC}"
fi

echo ""
echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}  同步完成${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""
echo -e "${CYAN}下一步建议：${NC}"
echo -e "${GRAY}  1. 测试应用是否正常运行：python app.py${NC}"
echo -e "${GRAY}  2. 检查自定义功能是否受影�?{NC}"
echo -e "${GRAY}  3. 如有问题，回滚到备份分支：git checkout backup-<timestamp>${NC}"
echo ""
