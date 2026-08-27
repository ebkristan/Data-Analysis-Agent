# ====================================
# 修正 Git 远程仓库配置
# ====================================

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  修正远程仓库配置" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# 当前配置
Write-Host "[当前配置]" -ForegroundColor Yellow
git remote -v
Write-Host ""

# 修正步骤
Write-Host "[修正步骤]" -ForegroundColor Cyan
Write-Host "1. 删除错误的 upstream 配置..." -ForegroundColor Gray

git remote remove upstream

Write-Host "2. 添加正确的 upstream (原作者仓库)..." -ForegroundColor Gray

git remote add upstream https://github.com/Zafer-Liu/Data-Analysis-Agent.git

Write-Host ""
Write-Host "[修正后的配置]" -ForegroundColor Green
git remote -v
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  配置修正完成！" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "现在你的配置是：" -ForegroundColor Green
Write-Host "  origin   → https://github.com/ebkristan/Data-Analysis-Agent.git  (你的Fork)" -ForegroundColor Green
Write-Host "  upstream → https://github.com/Zafer-Liu/Data-Analysis-Agent.git  (原作者)" -ForegroundColor Green
Write-Host ""

Write-Host "下一步操作：" -ForegroundColor Cyan
Write-Host "  1. 创建开发分支：git checkout -b custom-dev" -ForegroundColor Gray
Write-Host "  2. 推送到远程：git push -u origin custom-dev" -ForegroundColor Gray
Write-Host "  3. 开始开发..." -ForegroundColor Gray
Write-Host ""
