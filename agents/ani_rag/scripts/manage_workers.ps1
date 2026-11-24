param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action,
    [string]$PythonExe = '',
    [string]$Database = '',
    [string]$VectorDatabase = '',
    [string]$ImageDirectory = '',
    [string]$ScheduleState = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RunScript = Join-Path $PSScriptRoot 'run_worker.py'
$IndexerScript = Join-Path $PSScriptRoot 'run_vector_indexer.py'
$StateDirectory = Join-Path $ProjectRoot 'data\plans\workers'
$LogDirectory = Join-Path $ProjectRoot 'output\automatic_workers'
if (-not $Database) { $Database = Join-Path $ProjectRoot 'data\ani_rag_live_20261111.sqlite3' }
if (-not $VectorDatabase) { $VectorDatabase = Join-Path $ProjectRoot 'output\ani_rag_evidence_vectors_20261119.sqlite3' }
if (-not $ImageDirectory) { $ImageDirectory = Join-Path $ProjectRoot 'data\image_assets' }
if (-not $ScheduleState) { $ScheduleState = Join-Path $ProjectRoot 'data\plans\automatic_schedule_state_rotation7d.json' }
$Database = [System.IO.Path]::GetFullPath($Database)
$VectorDatabase = [System.IO.Path]::GetFullPath($VectorDatabase)
$ImageDirectory = [System.IO.Path]::GetFullPath($ImageDirectory)
$ScheduleState = [System.IO.Path]::GetFullPath($ScheduleState)
$Roles = @('crawler', 'generation', 'review', 'vector_indexer')

function Get-RoleProcesses([string]$Role) {
    $dbOption = if ($Role -eq 'vector_indexer') { '--vector-db' } else { '--db' }
    $targetFile = if ($Role -eq 'vector_indexer') { $VectorDatabase } else { $Database }
    $found = @()
    foreach ($process in Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) {
        if ($process.Name -notmatch '^pythonw?\.exe$') { continue }
        $commandLine = [string]$process.CommandLine
        if (-not $commandLine) { continue }
        if ($Role -eq 'vector_indexer') {
            $roleMatch = $commandLine.Contains('run_vector_indexer.py')
        } else {
            $rolePattern = '(?i)--role\s+["'']?' + [regex]::Escape($Role) + '(?:\s|["'']|$)'
            $roleMatch = $commandLine.Contains('run_worker.py') -and $commandLine -match $rolePattern
        }
        if (-not $roleMatch) { continue }
        $dbArg = [regex]::Match($commandLine, '(?:^|\s)' + $dbOption + '(?:=|\s+)(?:"([^"]+)"|''([^'']+)''|(\S+))')
        if (-not $dbArg.Success) { continue }
        $dbPath = @($dbArg.Groups[1].Value, $dbArg.Groups[2].Value, $dbArg.Groups[3].Value |
            Where-Object { $_ }) | Select-Object -First 1
        if (-not [System.IO.Path]::IsPathRooted($dbPath)) {
            $dbPath = Join-Path $ProjectRoot $dbPath
        }
        if ([System.IO.Path]::GetFullPath($dbPath) -ne $targetFile) { continue }
        $found += $process
    }
    # A Windows venv launcher stays alive while its child interpreter runs the same command.
    # Keep the launcher's PID for start/status/stop, and count unrelated workers separately.
    $launcherIds = @($found | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath -match '(?i)\\Scripts\\pythonw?\.exe$' -and
            (Test-Path -LiteralPath (Join-Path (Split-Path -Parent (Split-Path -Parent $_.ExecutablePath)) 'pyvenv.cfg'))
    } | ForEach-Object { $_.ProcessId })
    return $found | Where-Object { $_.ParentProcessId -notin $launcherIds }
}

function Get-WorkerCommandLine([string]$Role) {
    $pidFile = Join-Path $StateDirectory "$Role.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    $savedId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedId)) { return $null }
    return @(Get-RoleProcesses $Role | Where-Object { $_.ProcessId -eq $savedId }) | Select-Object -First 1
}

function Get-StopMarkerPath($Process) {
    $commandLine = [string]$Process.CommandLine
    $match = [regex]::Match($commandLine, '--stop-file(?:=|\s+)(?:"([^"]+)"|''([^'']+)''|(\S+))')
    if (-not $match.Success) { return $null }
    $value = @($match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value |
        Where-Object { $_ }) | Select-Object -First 1
    if ([System.IO.Path]::IsPathRooted($value)) { return [System.IO.Path]::GetFullPath($value) }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $value))
}

function Quote-Argument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

New-Item -ItemType Directory -Force -Path $StateDirectory, $LogDirectory | Out-Null

if ($Action -eq 'status') {
    foreach ($role in $Roles) {
        $processes = @(Get-RoleProcesses $role)
        if ($processes.Count -eq 0) {
            Write-Output "$role stopped"
            continue
        }
        foreach ($process in $processes) {
            $managed = (Get-WorkerCommandLine $role).ProcessId -eq $process.ProcessId
            $label = if ($managed) { 'managed' } else { 'unmanaged' }
            Write-Output "$role $label (PID $($process.ProcessId))"
        }
    }
    exit 0
}

if ($Action -eq 'start') {
    $lockPath = Join-Path $StateDirectory 'start.lock'
    $lockStream = $null
    try {
        try {
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
        } catch [System.IO.IOException] {
            throw 'Another worker start is already in progress; check status and start.lock.'
        }

        foreach ($role in $Roles) {
            $processes = @(Get-RoleProcesses $role)
            if ($processes.Count -gt 0) {
                $pids = ($processes | ForEach-Object { $_.ProcessId }) -join ', '
                throw "$role already has process(es) for this database (PID $pids); stop or reconcile them before start."
            }
        }
        if (-not (Test-Path -LiteralPath $Database)) { throw "RAG SQLite DB not found: $Database" }
        if (-not (Test-Path -LiteralPath $VectorDatabase)) { throw "RAG vector sidecar not found: $VectorDatabase" }
        if (-not (Test-Path -LiteralPath $RunScript)) { throw "Worker script not found: $RunScript" }
        if (-not (Test-Path -LiteralPath $IndexerScript)) { throw "Vector indexer script not found: $IndexerScript" }

        foreach ($role in $Roles) {
            Remove-Item -LiteralPath (Join-Path $StateDirectory "$role.stop") -Force -ErrorAction SilentlyContinue
        }
        if (-not $PythonExe) {
            $projectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
            $PythonExe = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { 'python' }
        }
        $python = (Get-Command $PythonExe -ErrorAction Stop).Source
        if ($python -match '(?i)\\Microsoft\\WindowsApps\\python(?:3(?:\.\d+)?)?\.exe$') {
            $launcher = (Get-Command py -ErrorAction SilentlyContinue).Source
            if (-not $launcher) {
                throw 'The python command resolves to the Windows app alias. Pass -PythonExe with the installed python.exe path.'
            }
            $resolvedPython = (& $launcher -3 -c 'import sys; print(sys.executable)' | Select-Object -Last 1).Trim()
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $resolvedPython)) {
                throw 'Could not resolve the installed Python interpreter through the Python launcher.'
            }
            $python = [System.IO.Path]::GetFullPath($resolvedPython)
        }
        $started = @()
        try {
            foreach ($role in @('crawler', 'generation', 'review')) {
                $arguments = @('-u', (Quote-Argument $RunScript), '--role', $role,
                    '--db', (Quote-Argument $Database), '--stop-file',
                    (Quote-Argument (Join-Path $StateDirectory "$role.stop")))
                if ($role -ne 'review') {
                    $arguments += @('--vector-db', (Quote-Argument $VectorDatabase))
                }
                if ($role -eq 'crawler') {
                    $arguments += @('--plan-config', (Quote-Argument (Join-Path $ProjectRoot 'config\automatic_plans.json')),
                        '--schedule-state', (Quote-Argument $ScheduleState))
                }
                $arguments += @('--image-dir', (Quote-Argument $ImageDirectory))
                $argumentLine = $arguments -join ' '
                $process = Start-Process -FilePath $python -ArgumentList $argumentLine -WorkingDirectory $ProjectRoot `
                    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDirectory "$role.stdout.log") `
                    -RedirectStandardError (Join-Path $LogDirectory "$role.stderr.log") -PassThru
                Set-Content -LiteralPath (Join-Path $StateDirectory "$role.pid") -Value $process.Id -Encoding ascii
                $started += $role
            }
            $arguments = @('-u', (Quote-Argument $IndexerScript), '--source-db', (Quote-Argument $Database),
                '--vector-db', (Quote-Argument $VectorDatabase), '--interval-seconds', '300', '--stop-file',
                (Quote-Argument (Join-Path $StateDirectory 'vector_indexer.stop')))
            $process = Start-Process -FilePath $python -ArgumentList ($arguments -join ' ') -WorkingDirectory $ProjectRoot `
                -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDirectory 'vector_indexer.stdout.log') `
                -RedirectStandardError (Join-Path $LogDirectory 'vector_indexer.stderr.log') -PassThru
            Set-Content -LiteralPath (Join-Path $StateDirectory 'vector_indexer.pid') -Value $process.Id -Encoding ascii
            $started += 'vector_indexer'
            Start-Sleep -Seconds 3
            foreach ($role in $Roles) {
                $processes = @(Get-RoleProcesses $role)
                $managed = Get-WorkerCommandLine $role
                if ($processes.Count -ne 1 -or -not $managed) {
                    throw "$role did not start as exactly one managed process; inspect $LogDirectory"
                }
            }
            Write-Output 'Started crawler, generation, review, and vector-indexer as separate hidden processes.'
            Write-Output "Logs: $LogDirectory"
        } catch {
            foreach ($role in $started) {
                Set-Content -LiteralPath (Join-Path $StateDirectory "$role.stop") -Value 'stop' -Encoding ascii
            }
            Start-Sleep -Seconds 3
            foreach ($role in $started) {
                $process = Get-WorkerCommandLine $role
                if ($process) {
                    Write-Warning "$role did not exit after startup rollback; PID $($process.ProcessId) remains."
                } else {
                    Remove-Item -LiteralPath (Join-Path $StateDirectory "$role.pid") -Force -ErrorAction SilentlyContinue
                }
            }
            throw
        }
    } finally {
        if ($lockStream) { $lockStream.Dispose() }
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

foreach ($role in $Roles) {
    $processes = @(Get-RoleProcesses $role)
    foreach ($process in $processes) {
        $marker = Get-StopMarkerPath $process
        if (-not $marker) {
            Write-Warning "$role PID $($process.ProcessId) has no stop-file argument; it will not be force-stopped."
            continue
        }
        $markerParent = Split-Path -Parent $marker
        New-Item -ItemType Directory -Force -Path $markerParent | Out-Null
        Set-Content -LiteralPath $marker -Value 'stop' -Encoding ascii
    }
}
$deadline = (Get-Date).AddSeconds(90)
do {
    $remaining = @($Roles | ForEach-Object { Get-RoleProcesses $_ } | Where-Object { $_ })
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

$remaining = @($Roles | ForEach-Object { Get-RoleProcesses $_ } | Where-Object { $_ })
if ($remaining.Count -gt 0) {
    $details = ($remaining | ForEach-Object { "$($_.ProcessId): $($_.Name)" }) -join '; '
    throw "Workers did not stop gracefully within 90 seconds: $details. No force kill was attempted."
}
foreach ($role in $Roles) {
    Remove-Item -LiteralPath (Join-Path $StateDirectory "$role.pid") -Force -ErrorAction SilentlyContinue
    Write-Output "$role stopped"
}
