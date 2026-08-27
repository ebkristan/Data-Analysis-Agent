# ====================================
# 上游同步脚本 - Windows PowerShell
# ====================================
# 用途：安全地拉取上游更新，同时保留你的自定义修改

param(
    [switch]$DryRun,      # 仅预览，不实际执行
    [switch]$Backup,      # 创建备份
    [switch]$Force        # 强制覆盖（危险！）
)

$ErrorActionPreference = "Stop"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  上游同步工具" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Git 仓库
if (-not (Test-Path ".git")) {
    Write-Host "[错误] 当前目录不是 Git 仓库！" -ForegroundColor Red
    exit 1
}

# 检查是否有未提交的修改
$status = git status --porcelain
if ($status -and -not $Force) {
    Write-Host "[警告] 存在未提交的修改！" -ForegroundColor Yellow
    Write-Host ""
    Write-Host $status
    Write-Host ""
    Write-Host "请先提交或暂存修改，或使用 -Force 参数强制执行" -ForegroundColor Yellow
    exit 1
}

# 创建备份（可选）
if ($Backup) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupBranch = "backup-$timestamp"
    
    Write-Host "[备份] 创建备份分支: $backupBranch" -ForegroundColor Green
    
    if (-not $DryRun) {
        git branch $backupBranch
        git push origin $backupBranch
    }
}

# 检查远程仓库配置
Write-Host "[检查] 验证远程仓库配置..." -ForegroundColor Cyan

$remotes = git remote -v | Out-String
if ($remotes -notmatch "upstream") {
    Write-Host "[警告] 未配置 upstream 远程仓库" -ForegroundColor Yellow
    Write-Host ""
    $confirm = Read-Host "是否现在配置？(y/n)"
    
    if ($confirm -eq "y") {
        Write-Host "[配置] 添加上游仓库..." -ForegroundColor Cyan
        
        if (-not $DryRun) {
            git remote add upstream https://github.com/Zafer-Liu/Data-Analysis-Agent.git
            Write-Host "[完成] upstream 已配置" -ForegroundColor Green
        }
    } else {
        Write-Host "[取消] 用户取消操作" -ForegroundColor Yellow
        exit 0
    }
}

# 获取当前分支
$currentBranch = git branch --show-current

Write-Host "[信息] 当前分支: $currentBranch" -ForegroundColor Cyan
Write-Host ""

# 拉取上游更新
Write-Host "[同步] 正在从上游拉取更新..." -ForegroundColor Cyan

if (-not $DryRun) {
    try {
        # 获取上游更新
        git fetch upstream
        
        # 切换到主分支（如果不在）
        if ($currentBranch -ne "main") {
            Write-Host "[切换] 切换到 main 分支..." -ForegroundColor Cyan
            git checkout main
        }
        
        # 合并上游更新
        Write-Host "[合并] 合并上游 main 分支..." -ForegroundColor Cyan
        git merge upstream/main --no-edit
        
        # 推送到你的远程
        Write-Host "[推送] 推送到你的远程仓库..." -ForegroundColor Cyan
        git push origin main
        
        # 切换回开发分支
        if ($currentBranch -ne "main") {
            Write-Host "[切换] 切换回 $currentBranch 分支..." -ForegroundColor Cyan
            git checkout $currentBranch
            
            Write-Host "[合并] 合并 main 到 $currentBranch..." -ForegroundColor Cyan
            git merge main --no-edit
        }
        
        Write-Host ""
        Write-Host "[成功] 上游更新已同步！" -ForegroundColor Green
        
    } catch {
        Write-Host ""
        Write-Host "[错误] 同步失败：$($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        Write-Host "可能的原因：" -ForegroundColor Yellow
        Write-Host "  1. 存在合并冲突，需要手动解决" -ForegroundColor Yellow
        Write-Host "  2. 网络连接问题" -ForegroundColor Yellow
        Write-Host "  3. 远程仓库配置错误" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "解决方案：" -ForegroundColor Cyan
        Write-Host "  - 查看冲突文件：git status" -ForegroundColor Cyan
        Write-Host "  - 解决冲突后：git add . && git commit" -ForegroundColor Cyan
        Write-Host "  - 继续合并：git merge --continue" -ForegroundColor Cyan
        
        exit 1
    }
} else {
    Write-Host "[预览模式] 将执行以下操作：" -ForegroundColor Yellow
    Write-Host "  1. git fetch upstream" -ForegroundColor Gray
    Write-Host "  2. git checkout main" -ForegroundColor Gray
    Write-Host "  3. git merge upstream/main" -ForegroundColor Gray
    Write-Host "  4. git push origin main" -ForegroundColor Gray
    Write-Host "  5. git checkout $currentBranch" -ForegroundColor Gray
    Write-Host "  6. git merge main" -ForegroundColor Gray
}

# 检查自定义文件
Write-Host ""
Write-Host "[检查] 扫描自定义文件..." -ForegroundColor Cyan

$customPaths = @(
    "extensions",
    "config/custom",
    ".env.custom"
)

$foundCustom = $false
foreach ($path in $customPaths) {
    if (Test-Path $path) {
        Write-Host "  ✓ 发现自定义内容: $path" -ForegroundColor Green
        $foundCustom = $true
    }
}

if (-not $foundCustom) {
    Write-Host "  ℹ 未发现自定义扩展目录" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  同步完成" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一步建议：" -ForegroundColor Cyan
Write-Host "  1. 测试应用是否正常运行：python app.py" -ForegroundColor Gray
Write-Host "  2. 检查自定义功能是否受影响" -ForegroundColor Gray
Write-Host "  3. 如有问题，回滚到备份分支：git checkout backup-<timestamp>" -ForegroundColor Gray
Write-Host ""
