$taskName   = "kk-GuelphSidewalksLayer"
$projectDir = $PSScriptRoot
$logFile    = "$projectDir\logs\scheduler.log"

if (-not (Test-Path "$projectDir\logs")) {
    New-Item -ItemType Directory -Path "$projectDir\logs" | Out-Null
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c cd /d `"$projectDir`" && python run.py update >> `"$logFile`" 2>&1"

# Daily, though the City adds only ~2 km of sidewalk a year: what moves is the
# gap page, which diffs against live OSM, and a day-old page is what mappers
# want after an evening's edits. A day with no City change costs nothing extra
# -- the download keeps the existing dated file when the features hash the
# same, and history replaces a same-day row rather than adding one.
#
# 21:15, not 16:30: the siblings each hold a weekly 16:30 or 19:30 slot (parks,
# buildings, bus stops, hydrants, trees; water, gardens, poster columns, bike)
# so no two drive tippecanoe in the same WSL instance at once. A daily run at
# either hour would land on all of them. 21:15 is clear of the 19:30 retries
# (last at 21:00) and its own retries end by 22:45.
$trigger  = New-ScheduledTaskTrigger -Daily -At "21:15"
# Restart on failure. 'update' exits 75 as soon as it sees no usable link
# instead of blocking on one (the ExecutionTimeLimit would kill a long wait
# anyway), so three tries half an hour apart turn a dead link at 21:15 into a
# run by 22:45 rather than a day's gap. Still clear of the 23:00 quiet window.
#
# It also exits 70 when no Overpass mirror answered and the gap page had to fall
# back to cached OSM. That publishes first, so the retry costs a rebuild and not
# the tiles; without it, two 504s in Aug 2026 left the page a fortnight stale
# with the task reporting success both times.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force

Write-Host "Scheduled '$taskName' to run daily at 21:15."
Write-Host "Log: $logFile"
