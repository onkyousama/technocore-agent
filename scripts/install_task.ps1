<#
  install_task.ps1 — register the roomwatch-onkyou daily job with Windows Task
  Scheduler (requirement #9).

    * runs once a day
    * "Run task as soon as possible after a scheduled start is missed"
      is enabled  (-StartWhenAvailable)
    * runs only while this user is logged on, so no password is stored

  Usage (from a normal PowerShell prompt — no admin needed):

      powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
      powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -At 13:30 -Test
      powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall
#>

param(
    [string]$At = "12:30",
    [switch]$Test,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "TechnocoreRoomwatchOnkyou"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Wrapper = Join-Path $PSScriptRoot "run_daily.cmd"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task '$TaskName'"
    } else {
        Write-Host "no scheduled task '$TaskName' to remove"
    }
    return
}

# --- sanity: the wrapper and Python must be usable --------------------
if (-not (Test-Path $Wrapper)) { throw "missing wrapper: $Wrapper" }
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $py) {
    $py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
}
if (-not $py) {
    Write-Warning "python not found on PATH; run_daily.cmd will fall back to a fixed location"
} else {
    Write-Host "python  : $($py.Source)"
}
Write-Host "project : $ProjectRoot"
Write-Host "wrapper : $Wrapper"

# --- (re)register -----------------------------------------------------
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument ('/c "' + $Wrapper + '"') -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# StartWhenAvailable only catches up missed runs within this window:
$settings.StartWhenAvailable = $true

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "roomwatch-onkyou: once-a-day signed observation of the technocore.chat room ecosystem. Local Ed25519 key; measured values only." | Out-Null

Write-Host "registered '$TaskName' — daily at $At, catch-up on missed runs enabled"

if ($Test) {
    Write-Host "`n--- test run ---"
    Start-ScheduledTask -TaskName $TaskName
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $state = (Get-ScheduledTask -TaskName $TaskName).State
        if ($state -ne "Running" -and $info.LastRunTime -gt (Get-Date).AddMinutes(-5)) {
            Write-Host ("LastRunTime   : {0}" -f $info.LastRunTime)
            Write-Host ("LastTaskResult: 0x{0:X} ({0})" -f $info.LastTaskResult)
            break
        }
    }
    Write-Host "`ntail of task_stdout.log:"
    $log = Join-Path $env:USERPROFILE ".technocore\logs\task_stdout.log"
    if (Test-Path $log) { Get-Content -Tail 25 -Encoding UTF8 $log }
}
