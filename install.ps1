#requires -Version 5.1
# Install/update the latest standalone Windows x64 binary; Python is not needed.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Say([string]$Message) { Write-Host "[opssum] $Message" }

$repository = if ($env:OPSSUM_REPO) { $env:OPSSUM_REPO } else { 'phungmanhquang/opssum' }
if ($repository -eq 'OWNER/REPO') {
    throw 'GitHub owner/repo chưa được cấu hình hợp lệ.'
}
if ($repository -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' -or $repository.Contains('..')) {
    throw "GitHub owner/repo không hợp lệ: $repository"
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows)) {
    throw 'install.ps1 chỉ hỗ trợ Windows. Linux/macOS hãy dùng install.sh.'
}
$osArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($osArchitecture -ne 'X64') {
    throw "Chưa có binary Windows cho CPU $osArchitecture; hiện hỗ trợ Windows x64."
}

$asset = 'opssum-windows-x64.exe'
$baseUrl = "https://github.com/$repository/releases/latest/download"
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("opssum-install-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $temporary
$stage = $null
try {
    $downloaded = Join-Path $temporary $asset
    $checksum = Join-Path $temporary "$asset.sha256"
    Say "Đang tải $asset từ GitHub Releases mới nhất..."
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$asset" -OutFile $downloaded
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$asset.sha256" -OutFile $checksum

    $line = Get-Content -LiteralPath $checksum -TotalCount 1
    $pattern = '^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($asset) + '$'
    if ($line -notmatch $pattern) { throw "File SHA256 không hợp lệ cho $asset." }
    $expected = $Matches[1].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $downloaded -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'SHA256 không khớp; bản cài hiện tại được giữ nguyên.' }

    Unblock-File -LiteralPath $downloaded -ErrorAction SilentlyContinue
    $version = & $downloaded --version 2>&1
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^opssum ') {
        throw 'Binary không chạy trên máy này; bản cài hiện tại được giữ nguyên.'
    }

    $binDir = Join-Path $HOME '.local\bin'
    if (Test-Path -LiteralPath $binDir -PathType Leaf) { throw "$binDir là file, không thể cài đặt." }
    $null = New-Item -ItemType Directory -Path $binDir -Force
    $destination = Join-Path $binDir 'opssum.exe'
    if (Test-Path -LiteralPath $destination -PathType Container) { throw "$destination là thư mục, không thể ghi đè." }

    if ((Test-Path -LiteralPath $destination -PathType Leaf) -and
        ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -eq $actual)) {
        Say "$asset đã là bản mới nhất; không cần thay binary."
    } else {
        $stage = Join-Path $binDir ('.opssum-install-' + [guid]::NewGuid().ToString('N') + '.exe')
        Copy-Item -LiteralPath $downloaded -Destination $stage
        if (Test-Path -LiteralPath $destination) {
            $existing = Get-Item -LiteralPath $destination -Force
            if (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$destination là symlink; hãy kiểm tra bản cài cũ trước khi thay."
            }
            $backup = Join-Path $temporary 'previous.exe'
            [System.IO.File]::Replace($stage, $destination, $backup)
        } else {
            [System.IO.File]::Move($stage, $destination)
        }
        $stage = $null
        Say "Đã cài/cập nhật: $destination"
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -notcontains $binDir) {
        $newPath = if ($userPath) { "$binDir;$userPath" } else { $binDir }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Say "Đã thêm $binDir vào User PATH; mở terminal mới để nhận PATH cập nhật."
    }
    if (@($env:Path -split ';') -notcontains $binDir) { $env:Path = "$binDir;$env:Path" }
    Say 'Chạy: opssum init --examples (lần đầu), sau đó opssum'
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Force }
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
