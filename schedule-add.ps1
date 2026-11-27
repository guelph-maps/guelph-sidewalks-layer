$taskName   = "kk-GuelphSidewalksLayer"
$projectDir = $PSScriptRoot
$logFile    = "$projectDir\logs\scheduler.log"

if (-not (Test-Path "$projectDir\logs")) {
    New-Item -ItemType Directory -Path "$projectDir\logs" | Out-Null
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$projectDir\run-scheduled.cmd`" >> `"$logFile`" 2>&1`""

# Weekly is enough: the City adds ~2 km of sidewalk a year, and its service
# carries no edit date to poll anyway. The gap page is what actually moves
# week to week -- it diffs against live OSM. Each sibling has its own 16:30
# weekday so no two drive tippecanoe in the same WSL instance at once:
# buildings Wednesday, bus stops Thursday, hydrants Friday, trees Sunday --
# sidewalks takes Saturday. (It ran daily at 21:15 from 2026-11-17 to
# 2026-11-27; guelph-parks holds that daily slot now.)
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "16:30"
# Retries live in run-scheduled.cmd, not here: 'update' exits 75 when it sees no
# usable link, and 70 when no Overpass mirror answered and the gap page fell back
# to cached OSM (published first). The wrapper tries either again up to twice, half
# an hour apart, and the 3-hour limit leaves room for that. RestartCount covers only
# a launch that fails; Task Scheduler never re-runs on an exit code.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3) -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force

Write-Host "Scheduled '$taskName' to run weekly on Saturday at 16:30."
Write-Host "Log: $logFile"
