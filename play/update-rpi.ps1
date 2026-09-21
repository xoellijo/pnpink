[CmdletBinding()]
param(
    [string]$Target = 'jl@192.168.1.5',
    [string]$RemoteRoot = '/home/jl/Projects/pnpplay',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$PlayRoot = $PSScriptRoot

function Invoke-Checked {
    param(
        [string]$Command,
        [string[]]$CommandArgs
    )

    & $Command @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipBuild) {
    $portableNode = 'C:\Portables\node'
    if (Test-Path -LiteralPath (Join-Path $portableNode 'npm.cmd')) {
        $env:Path = "$portableNode;$env:Path"
        $npm = Join-Path $portableNode 'npm.cmd'
    } else {
        $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    }

    Push-Location $PlayRoot
    try {
        Invoke-Checked $npm @('run', 'build')
    } finally {
        Pop-Location
    }
}

Invoke-Checked 'ssh' @(
    $Target,
    "mkdir -p '$RemoteRoot/server/boardzilla_lite/web' '$RemoteRoot/games/pnpink-play-demo/build/game' '$RemoteRoot/games/pnpink-play-demo/build/ui' '$RemoteRoot/games/pnpink-poker-table/build/game' '$RemoteRoot/games/pnpink-poker-table/build/ui'"
)

$serverFiles = @(Get-ChildItem -LiteralPath (Join-Path $PlayRoot 'server/boardzilla_lite') -Filter '*.py' -File | ForEach-Object FullName)
Invoke-Checked 'scp' @($serverFiles + "${Target}:$RemoteRoot/server/boardzilla_lite/")

$webFiles = @(Get-ChildItem -LiteralPath (Join-Path $PlayRoot 'server/boardzilla_lite/web') -File | ForEach-Object FullName)
Invoke-Checked 'scp' @($webFiles + "${Target}:$RemoteRoot/server/boardzilla_lite/web/")

Invoke-Checked 'scp' @((Join-Path $PlayRoot 'standard.yaml'), "${Target}:$RemoteRoot/")

$games = @(
    @{ Name = 'pnpink-play-demo'; Source = $PlayRoot },
    @{ Name = 'pnpink-poker-table'; Source = Join-Path $PlayRoot 'examples/poker' }
)

foreach ($game in $games) {
    $remoteGame = "$RemoteRoot/games/$($game.Name)"
    $configFiles = @('game.json', 'play.yaml', 'play.json') |
        ForEach-Object { Join-Path $game.Source $_ } |
        Where-Object { Test-Path -LiteralPath $_ }
    Invoke-Checked 'scp' @($configFiles + "${Target}:$remoteGame/")

    $assets = Join-Path $game.Source 'assets'
    if (Test-Path -LiteralPath $assets) {
        Invoke-Checked 'scp' @('-r', $assets, "${Target}:$remoteGame/")
    }

    $gameBuildFiles = @(Get-ChildItem -LiteralPath (Join-Path $PlayRoot 'build/game') -File | ForEach-Object FullName)
    Invoke-Checked 'scp' @($gameBuildFiles + "${Target}:$remoteGame/build/game/")

    $uiBuildFiles = @(Get-ChildItem -LiteralPath (Join-Path $PlayRoot 'build/ui') -File | ForEach-Object FullName)
    Invoke-Checked 'scp' @($uiBuildFiles + "${Target}:$remoteGame/build/ui/")
    $uiAssets = Join-Path $PlayRoot 'build/ui/assets'
    if (Test-Path -LiteralPath $uiAssets) {
        Invoke-Checked 'scp' @('-r', $uiAssets, "${Target}:$remoteGame/build/ui/")
    }
}

Invoke-Checked 'ssh' @(
    '-t',
    $Target,
    'sudo systemctl restart pnpplay || exit 1; for attempt in $(seq 1 20); do if curl -fsS http://127.0.0.1:8080/games >/dev/null; then exit 0; fi; sleep 1; done; sudo systemctl status pnpplay --no-pager; sudo journalctl -u pnpplay -n 30 --no-pager; exit 1'
)

Write-Host "PnPPlay updated successfully on $Target"
