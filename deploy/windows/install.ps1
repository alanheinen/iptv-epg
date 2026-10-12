<#
.SYNOPSIS
Install Signal TV Guide on Windows as a background task that starts at logon.

.DESCRIPTION
Copies the app to %LOCALAPPDATA%\SignalTV and registers a scheduled task,
"Signal TV Guide", that runs it with pythonw.exe (no console window) whenever
you log on, and starts it now. Logs go to %LOCALAPPDATA%\SignalTV\signal-tv.log.
Needs Python 3.9 or newer from python.org; nothing else. Run it again to
update or change settings.

.PARAMETER Countries
iptv-org playlist prefixes to build, comma-separated. The UK is "uk".

.PARAMETER Port
Port to serve on.

.PARAMETER Lan
Listen on every interface so other devices on your network can watch, and
allow the port through Windows Firewall on Private networks only. Needs an
elevated (administrator) PowerShell. Never expose the port to the internet:
the stream proxy would become an open relay.

.PARAMETER Uninstall
Stop and remove the task, the firewall rule and %LOCALAPPDATA%\SignalTV.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Countries us,ca,uk
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Countries = "us",
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [switch]$Lan,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "Signal TV Guide"
$RuleName = "Signal TV Guide"
$Root = Join-Path $env:LOCALAPPDATA "SignalTV"
$App = Join-Path $Root "app"
$Data = Join-Path $Root "data"
$Log = Join-Path $Root "signal-tv.log"
$Src = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Stop-SignalTask {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t -and $PSCmdlet.ShouldProcess($TaskName, "Stop scheduled task")) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        # Stopping the task ends pythonw.exe, but its child guide build can
        # outlive it; end any process still running from the install folder.
        Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($App) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    $t
}

function Find-Python {
    # The py launcher first, then python.exe on PATH. The Microsoft Store's
    # "python.exe" alias isn't Python, so anything that can't run fails here.
    foreach ($c in @(@("py", "-3"), @("python"))) {
        $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $pyArgs = @($c | Select-Object -Skip 1) + @("-c", "import sys; print(sys.executable); print(int(sys.version_info >= (3, 9)))")
        try { $out = & $cmd.Source @pyArgs 2>$null } catch { continue }
        if ($LASTEXITCODE -ne 0 -or -not $out -or $out.Count -lt 2) { continue }
        if ($out[1] -ne "1") { throw "$($out[0]) is older than Python 3.9; install a newer Python from python.org" }
        $pyw = Join-Path (Split-Path $out[0]) "pythonw.exe"
        if (Test-Path $pyw) { return $pyw }
    }
    throw "Python 3.9 or newer wasn't found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again."
}

if ($Uninstall) {
    if (Stop-SignalTask) {
        if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    }
    if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
        if (Test-Admin) {
            if ($PSCmdlet.ShouldProcess($RuleName, "Remove firewall rule")) { Remove-NetFirewallRule -DisplayName $RuleName }
        } else {
            Write-Warning "The firewall rule '$RuleName' needs an administrator PowerShell to remove; run -Uninstall again from one."
        }
    }
    if ((Test-Path $Root) -and $PSCmdlet.ShouldProcess($Root, "Remove folder")) { Remove-Item -Recurse -Force $Root }
    Write-Host "Removed Signal TV Guide."
    return
}

if ($Countries -notmatch '^[A-Za-z]{2}(,[A-Za-z]{2})*$') { throw "-Countries takes two-letter playlist prefixes like us,ca,uk" }
if ($Lan -and -not (Test-Admin)) { throw "-Lan adds a firewall rule; run this from an administrator PowerShell." }
if (-not (Test-Path (Join-Path $Src "scripts\serve.py"))) { throw "Run this from a copy of the repository ($Src has no scripts\serve.py)." }
$Pythonw = Find-Python
$HostAddr = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

[void](Stop-SignalTask)
if ($PSCmdlet.ShouldProcess($App, "Copy the app")) {
    if (Test-Path $App) { Remove-Item -Recurse -Force $App }
    New-Item -ItemType Directory -Force (Join-Path $App "web"), $Data | Out-Null
    Copy-Item -Recurse (Join-Path $Src "scripts") $App
    Copy-Item (Join-Path $Src "web\index.html"), (Join-Path $Src "web\app.css"), (Join-Path $Src "web\app.js") (Join-Path $App "web")
    Remove-Item -Recurse -Force (Join-Path $App "scripts\__pycache__") -ErrorAction SilentlyContinue
}

$serve = Join-Path $App "scripts\serve.py"
$argline = "`"$serve`" --host $HostAddr --port $Port --data `"$Data`" --refresh 1 --countries $($Countries.ToLower()) --log `"$Log`""
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument $argline -WorkingDirectory $App
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# No time limit (the default stops a task after 3 days), keep running on
# battery, and retry if it fails to start.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -StartWhenAvailable
if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description "Signal TV Guide: IPTV programme guide and stream proxy" -Force | Out-Null
}

$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($Lan) {
    if ($rule) { Remove-NetFirewallRule -DisplayName $RuleName }
    if ($PSCmdlet.ShouldProcess($RuleName, "Allow TCP $Port on Private networks")) {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
            -Program $Pythonw -Profile Private | Out-Null
    }
} elseif ($rule) {
    if (Test-Admin) { Remove-NetFirewallRule -DisplayName $RuleName }
    else { Write-Warning "A firewall rule from an earlier -Lan install is still there; remove it from an administrator PowerShell with -Uninstall or Remove-NetFirewallRule -DisplayName '$RuleName'." }
}

if ($PSCmdlet.ShouldProcess($TaskName, "Start scheduled task")) { Start-ScheduledTask -TaskName $TaskName }

if ($WhatIfPreference) { return }
$where = if ($Lan) { $env:COMPUTERNAME } else { "127.0.0.1" }
Write-Host "Signal TV Guide is running and starts when you log on. The first guide build takes about a minute; then open"
Write-Host "  http://${where}:$Port/"
Write-Host "Logs: $Log"
