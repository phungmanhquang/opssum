#requires -Version 5.1
# Install/update the latest standalone Windows x64 binary; Python is not needed.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Say([string]$Message) { Write-Host "[opssum] $Message" }

$repository = if ($env:OPSSUM_REPO) { $env:OPSSUM_REPO } else { 'phungmanhquang/opssum' }
if ($repository -eq 'OWNER/REPO') {
    throw 'GitHub owner/repo is not configured correctly.'
}
if ($repository -notmatch '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' -or $repository.Contains('..')) {
    throw "Invalid GitHub owner/repo: $repository"
}
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
        [System.Runtime.InteropServices.OSPlatform]::Windows)) {
    throw 'install.ps1 supports Windows only. Linux/macOS users should use install.sh.'
}
$osArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
if ($osArchitecture -ne 'X64') {
    throw "No Windows binary is available for CPU $osArchitecture; Windows x64 is currently supported."
}

$asset = 'opssum-windows-x64.exe'
$baseUrl = "https://github.com/$repository/releases/latest/download"
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("opssum-install-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $temporary
$stage = $null
try {
    $downloaded = Join-Path $temporary $asset
    $checksum = Join-Path $temporary "$asset.sha256"
    Say "Downloading $asset from the latest GitHub Release..."
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$asset" -OutFile $downloaded
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$asset.sha256" -OutFile $checksum

    $line = Get-Content -LiteralPath $checksum -TotalCount 1
    $pattern = '^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($asset) + '$'
    if ($line -notmatch $pattern) { throw "Invalid SHA256 file for $asset." }
    $expected = $Matches[1].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $downloaded -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'SHA256 mismatch; the current installation was kept unchanged.' }

    Unblock-File -LiteralPath $downloaded -ErrorAction SilentlyContinue
    $version = & $downloaded --version 2>&1
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^opssum ') {
        throw 'The binary cannot run on this machine; the current installation was kept unchanged.'
    }

    $binDir = Join-Path $HOME '.local\bin'
    if (Test-Path -LiteralPath $binDir -PathType Leaf) { throw "$binDir is a file and cannot be used as an install directory." }
    $null = New-Item -ItemType Directory -Path $binDir -Force
    $destination = Join-Path $binDir 'opssum.exe'
    if (Test-Path -LiteralPath $destination -PathType Container) { throw "$destination is a directory and cannot be overwritten." }

    if ((Test-Path -LiteralPath $destination -PathType Leaf) -and
        ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -eq $actual)) {
        Say "$asset is already up to date; no binary replacement is needed."
    } else {
        $stage = Join-Path $binDir ('.opssum-install-' + [guid]::NewGuid().ToString('N') + '.exe')
        Copy-Item -LiteralPath $downloaded -Destination $stage
        if (Test-Path -LiteralPath $destination) {
            $existing = Get-Item -LiteralPath $destination -Force
            if (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$destination is a symlink; review the existing installation before replacing it."
            }
            $backup = Join-Path $temporary 'previous.exe'
            [System.IO.File]::Replace($stage, $destination, $backup)
        } else {
            [System.IO.File]::Move($stage, $destination)
        }
        $stage = $null
        Say "Installed/updated: $destination"
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($userPath -split ';' | Where-Object { $_ })
    if ($entries -notcontains $binDir) {
        $newPath = if ($userPath) { "$binDir;$userPath" } else { $binDir }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Say "Added $binDir to the User PATH; open a new terminal to use the updated PATH."
    }
    if (@($env:Path -split ';') -notcontains $binDir) { $env:Path = "$binDir;$env:Path" }
    Say 'Run: opssum init --examples (first time), then opssum'
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Force }
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
