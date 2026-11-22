param([string]$Message = "update")
Set-Location $PSScriptRoot
$changes = git status --porcelain
if (-not $changes) { Write-Host "Нет изменений для коммита"; exit 0 }
git add -A
git commit -m $Message
git push origin main