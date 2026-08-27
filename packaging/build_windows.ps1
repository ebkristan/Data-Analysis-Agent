[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '1.2.0',
    [string]$WorkRoot,
    [string]$InnoCompiler,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $IsWindows) {
    throw 'Windows packages must be built on a native Windows runner.'
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'The Windows x64 package requires a 64-bit operating system.'
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
if (-not $WorkRoot) {
    # Keep this path short: PyTorch ships deeply nested license files that Inno Setup must compress.
    $WorkRoot = Join-Path $buildRoot 'w'
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
if (-not $WorkRoot.StartsWith($buildRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkRoot must stay below the project build directory.'
}

if (Test-Path -LiteralPath $WorkRoot) {
    Remove-Item -LiteralPath $WorkRoot -Recurse -Force
}
$staging = Join-Path $WorkRoot 'staging'
$pyiWork = Join-Path $WorkRoot 'pyinstaller-work'
$pyiDist = Join-Path $WorkRoot 'pyinstaller-dist'
$onedir = Join-Path $pyiDist 'BusinessAnalyticsAgent'
$runtimeSmoke = Join-Path $WorkRoot 'runtime-smoke'
$installerOutput = Join-Path $WorkRoot 'installer'
$reports = Join-Path $WorkRoot 'reports'
New-Item -ItemType Directory -Path $reports, $installerOutput | Out-Null

function Invoke-CheckedPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

Invoke-CheckedPython (Join-Path $projectRoot 'packaging\build_manifest.py') `
    --source $projectRoot --destination $staging `
    --manifest (Join-Path $reports 'staging-manifest.json')
Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
    $staging --report (Join-Path $reports 'staging-audit.json')

$env:BAA_STAGING_ROOT = $staging
Invoke-CheckedPython -m PyInstaller --clean --noconfirm `
    --distpath $pyiDist --workpath $pyiWork `
    (Join-Path $projectRoot 'packaging\business_agent.spec')
Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
    $onedir --report (Join-Path $reports 'onedir-audit.json')

$env:BAA_DATA_DIR = $runtimeSmoke
$env:BAA_NO_BROWSER = '1'
$env:BAA_ONEDIR_SELF_TEST = '1'
$env:BAA_CLEANUP_DISABLED = '1'
$process = Start-Process -FilePath (Join-Path $onedir 'BusinessAnalyticsAgent.exe') `
    -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) {
    throw "Frozen self-test failed with exit code $($process.ExitCode)."
}
$smokeReport = Join-Path $runtimeSmoke 'outputs\build-smoke.json'
if (-not (Test-Path -LiteralPath $smokeReport)) {
    throw 'Frozen self-test did not create its report.'
}
$smoke = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json
if (-not $smoke.ok -or -not $smoke.frozen) {
    throw 'Frozen self-test report is not successful.'
}
Copy-Item -LiteralPath $smokeReport -Destination (Join-Path $reports 'frozen-smoke.json')

Remove-Item Env:BAA_ONEDIR_SELF_TEST -ErrorAction SilentlyContinue
if ($PrepareOnly) {
    Write-Host "Audited onedir ready for manual Inno compilation: $onedir"
    return
}
if (-not $InnoCompiler) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $InnoCompiler = $command.Source
    } else {
        $InnoCompiler = @(
            'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
            'C:\Program Files\Inno Setup 6\ISCC.exe'
        ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw 'Inno Setup 6 compiler (ISCC.exe) was not found.'
}

$iconFile = Join-Path $projectRoot 'installer\icon.ico'
if (-not (Test-Path -LiteralPath $iconFile -PathType Leaf)) {
    throw "Installer icon was not found: $iconFile"
}

# Inno Setup still uses legacy path handling while compressing. PyTorch ships
# license paths that exceed MAX_PATH under the GitHub Actions checkout, so map
# the existing work directory to a temporary drive for the compile step only.
$innoDrive = @('Z', 'Y', 'X', 'W', 'V', 'U', 'T') |
    Where-Object { -not (Test-Path -LiteralPath "${_}:\") } |
    Select-Object -First 1
if (-not $innoDrive) {
    throw 'No free drive letter is available for the Inno Setup build.'
}
$innoDriveName = "${innoDrive}:"
& subst.exe $innoDriveName $WorkRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to map $WorkRoot to $innoDriveName for Inno Setup."
}

$innoExitCode = $null
try {
    $innoRoot = "${innoDriveName}\"
    $innoOnedir = Join-Path $innoRoot 'pyinstaller-dist\BusinessAnalyticsAgent'
    $innoInstallerOutput = Join-Path $innoRoot 'installer'
    & $InnoCompiler "/DOnedirSource=$innoOnedir" `
        "/DInstallerOutputDir=$innoInstallerOutput" `
        "/DIconFilePath=$iconFile" "/DAppVersion=$Version" `
        (Join-Path $projectRoot 'installer\setup.iss')
    $innoExitCode = $LASTEXITCODE
} finally {
    & subst.exe $innoDriveName /D | Out-Null
}
if ($innoExitCode -ne 0) {
    throw "Inno Setup failed with exit code $innoExitCode."
}

$installer = Join-Path $installerOutput 'BusinessAnalyticsAgent-Windows-x64.exe'
if (-not (Test-Path -LiteralPath $installer)) {
    throw 'Inno Setup did not produce the expected installer.'
}
Invoke-CheckedPython (Join-Path $projectRoot 'packaging\audit_artifact.py') `
    $installer --report (Join-Path $reports 'installer-audit.json')

$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$release = [ordered]@{
    schema_version = 1
    version = $Version
    platform = 'windows-x64'
    filename = [System.IO.Path]::GetFileName($installer)
    size = (Get-Item -LiteralPath $installer).Length
    sha256 = $hash
    unsigned = $true
}
$release | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reports 'release.json') -Encoding utf8
Write-Host "Windows installer ready: $installer"
Write-Host "SHA-256: $hash"
