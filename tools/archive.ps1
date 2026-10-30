<#
  Glide to 60 - keeps copies of what the game writes, so nothing is lost between sessions.

  Why: on the Forever beta the client does not read SavedVariables back after a cold start, so
  every logout overwrites GlideRecorder.lua with only that session. And a client patch can wipe
  Cache\WDB. This script copies both into data\archive whenever they change.

  Usage (PowerShell, from the "Glide to 60" folder):
    powershell -ExecutionPolicy Bypass -File tools\archive.ps1          # watch while you play
    powershell -ExecutionPolicy Bypass -File tools\archive.ps1 -Once    # one pass
#>
param([switch]$Once, [int]$Every = 20)

$Root = Split-Path -Parent $PSScriptRoot
$Config = Get-Content (Join-Path $Root "config.json") -Raw | ConvertFrom-Json
$Wow = $Config.wow_dir
$SvOut = Join-Path $Root "data\archive\sv"
$WdbOut = Join-Path $Root "data\archive\wdb"
New-Item -ItemType Directory -Force -Path $SvOut, $WdbOut | Out-Null
$seen = @{}

function Hash($p) { (Get-FileHash -Algorithm SHA1 -LiteralPath $p).Hash }

function Archive-Once {
    # SavedVariables: every distinct version is kept
    Get-ChildItem -Path (Join-Path $Wow "WTF\Account\*\SavedVariables") -Include "Glide.lua", "GlideRecorder.lua", "RXPGuides.lua" -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object {
        $h = Hash $_.FullName
        if ($seen[$_.FullName] -eq $h) { return }
        $seen[$_.FullName] = $h
        $acct = $_.Directory.Parent.Name -replace '[^\w]', '_'
        $stamp = $_.LastWriteTime.ToString("yyyyMMdd-HHmmss")
        $dest = Join-Path $SvOut ("{0}_{1}_{2}.lua" -f $_.BaseName, $acct, $stamp)
        if (-not (Test-Path $dest)) {
            Copy-Item -LiteralPath $_.FullName -Destination $dest
            Write-Host ("[{0}] saved {1}" -f (Get-Date -Format HH:mm:ss), (Split-Path -Leaf $dest))
        }
    }
    # WDB cache: one folder per day; a smaller file (cache wiped by a patch) starts a new folder
    $wdb = Join-Path $Wow "Cache\WDB"
    if (-not (Test-Path $wdb)) { return }
    Get-ChildItem -Path $wdb -Filter *.wdb -Recurse | ForEach-Object {
        $h = Hash $_.FullName
        if ($seen[$_.FullName] -eq $h) { return }
        $seen[$_.FullName] = $h
        $day = Join-Path $WdbOut (Get-Date -Format "yyyyMMdd")
        $dest = Join-Path $day $_.Name
        if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt $_.Length)) {
            $day = Join-Path $WdbOut (Get-Date -Format "yyyyMMdd-HHmmss")
            $dest = Join-Path $day $_.Name
        }
        New-Item -ItemType Directory -Force -Path $day | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        Write-Host ("[{0}] cache {1} ({2:N0} KB)" -f (Get-Date -Format HH:mm:ss), $_.Name, ($_.Length / 1KB))
    }
}

Write-Host "Glide archiver: $Wow -> $Root\data\archive"
do {
    Archive-Once
    if (-not $Once) { Start-Sleep -Seconds $Every }
} while (-not $Once)
