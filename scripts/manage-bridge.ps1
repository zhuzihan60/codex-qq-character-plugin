# Windows-only process manager. Does not install a task/service or modify credentials.
# Usage: .\scripts\manage-bridge.ps1 start|status|stop|run|doctor
# run is a foreground entry point; start launches it in a hidden PowerShell window.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'status', 'stop', 'run', 'doctor')]
    [string]$Action = 'status',
    [ValidatePattern('^[a-f0-9]{32}$')]
    [string]$RunId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This manager requires Windows and the current Windows user DPAPI store.'
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$managerPath = [IO.Path]::GetFullPath($PSCommandPath)
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$configPath = Join-Path $projectRoot 'config\local.json'
$stateRoot = Join-Path $projectRoot '.state'
$statePath = Join-Path $stateRoot 'bridge-process.json'
$secretPath = Join-Path $stateRoot 'secrets\onebot.dpapi'
$leasePath = Join-Path $stateRoot 'bridge-manager.lock'
$logRoot = Join-Path $stateRoot 'logs'
$managerExecutable = (Get-Process -Id $PID).Path
$approvedPythonPaths = @($pythonPath)
# Windows venv python.exe can be a redirector that starts the base interpreter.
# Read only this project's venv metadata, never user profiles or credential files.
$venvMetadata = Join-Path $projectRoot '.venv\pyvenv.cfg'
if (Test-Path -LiteralPath $venvMetadata -PathType Leaf) {
    foreach ($line in [IO.File]::ReadAllLines($venvMetadata)) {
        if ($line -match '^executable\s*=\s*(.+)$') {
            $approvedPythonPaths += [IO.Path]::GetFullPath($Matches[1].Trim())
        }
    }
}

# Native argument parsing prevents a substring in an unrelated process from matching.
if (-not ('QQCharacter.NativeCommandLine' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace QQCharacter {
    public static class NativeCommandLine {
        [DllImport("shell32.dll", SetLastError=true)]
        static extern IntPtr CommandLineToArgvW(
            [MarshalAs(UnmanagedType.LPWStr)] string commandLine, out int count);
        [DllImport("kernel32.dll")]
        static extern IntPtr LocalFree(IntPtr value);
        public static string[] Split(string commandLine) {
            int count;
            IntPtr pointer = CommandLineToArgvW(commandLine, out count);
            if (pointer == IntPtr.Zero) throw new InvalidOperationException("Cannot parse process arguments");
            try {
                string[] result = new string[count];
                for (int i=0; i<count; i++)
                    result[i] = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer, i * IntPtr.Size));
                return result;
            } finally { LocalFree(pointer); }
        }
    }
}
'@
}

function Get-TextHash([string]$Value) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-ProcessIdentity([int]$ProcessId) {
    # WMI can return a partial row while a process is starting or exiting. Never
    # weaken identity checks: re-query briefly, and treat a disappeared PID as gone.
    $item = $null
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $item = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if (-not $item) { return $null }
        if ($item.ExecutablePath -and $item.CommandLine -and $item.CreationDate) { break }
        $stillRunning = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $stillRunning) { return $null }
        $stillRunning.Dispose()
        Start-Sleep -Milliseconds 100
    }
    if (-not $item.ExecutablePath -or -not $item.CommandLine -or -not $item.CreationDate) {
        throw "Cannot verify process identity for PID $ProcessId under the current Windows account."
    }
    return [pscustomobject]@{
        id = [int]$item.ProcessId
        parent = [int]$item.ParentProcessId
        path = [IO.Path]::GetFullPath([string]$item.ExecutablePath)
        created = $item.CreationDate.ToUniversalTime().ToString('o')
        command_hash = Get-TextHash ([string]$item.CommandLine)
        arguments = @([QQCharacter.NativeCommandLine]::Split([string]$item.CommandLine))
    }
}

function Get-IdentityRecord($Identity) {
    # Never persist raw environment or process command lines.
    return [ordered]@{ id = $Identity.id; parent = $Identity.parent; path = $Identity.path;
        created = $Identity.created; command_hash = $Identity.command_hash }
}

function Test-RecordedIdentity($Recorded, $Actual) {
    if ($null -eq $Recorded -or $null -eq $Actual) { return $false }
    # PowerShell 7 converts JSON ISO timestamps to DateTime; Windows PowerShell
    # keeps strings. Compare full UTC ticks, not culture-dependent string casts.
    try {
        $recordedTime = $(if ($Recorded.created -is [DateTime]) { $Recorded.created.ToUniversalTime().Ticks }
            else { [DateTimeOffset]::Parse([string]$Recorded.created, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).UtcDateTime.Ticks })
        $actualTime = $(if ($Actual.created -is [DateTime]) { $Actual.created.ToUniversalTime().Ticks }
            else { [DateTimeOffset]::Parse([string]$Actual.created, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).UtcDateTime.Ticks })
    } catch { return $false }
    return ($null -ne $Recorded -and $null -ne $Actual -and
        [int]$Recorded.id -eq $Actual.id -and [int]$Recorded.parent -eq $Actual.parent -and
        [string]$Recorded.path -ieq $Actual.path -and $recordedTime -eq $actualTime -and
        [string]$Recorded.command_hash -ceq $Actual.command_hash)
}

function Test-BridgeArguments($Identity) {
    if (-not $Identity -or -not ($approvedPythonPaths -icontains $Identity.path)) { return $false }
    $tokens = @($Identity.arguments)
    return ($tokens.Count -eq 6 -and $tokens[1] -ceq '-m' -and $tokens[2] -ceq 'qq_character' -and
        $tokens[3] -ceq '--config' -and $tokens[4] -ieq $configPath -and $tokens[5] -ceq 'serve')
}

function Get-BridgeWorkers($Launcher) {
    if (-not $Launcher) { return @() }
    $found = @()
    foreach ($item in @(Get-CimInstance -ClassName Win32_Process -Filter "ParentProcessId = $($Launcher.id)")) {
        $identity = Get-ProcessIdentity ([int]$item.ProcessId)
        if ($identity -and $identity.parent -eq $Launcher.id -and (Test-BridgeArguments $identity)) { $found += $identity }
    }
    return $found
}

function Stop-OwnedBridge($LauncherRecord, $WorkerRecords) {
    # Stop the actual venv interpreter before its redirector. Never taskkill /IM or /T.
    $launcher = Get-ProcessIdentity ([int]$LauncherRecord.id)
    $targets = @($WorkerRecords)
    if ((Test-RecordedIdentity $LauncherRecord $launcher) -and (Test-BridgeArguments $launcher)) {
        foreach ($worker in @(Get-BridgeWorkers $launcher)) { $targets += Get-IdentityRecord $worker }
        $targets += $LauncherRecord
    }
    $seen = @{}
    foreach ($target in $targets) {
        if (-not $target -or $seen.ContainsKey([int]$target.id)) { continue }
        $seen[[int]$target.id] = $true
        $actual = Get-ProcessIdentity ([int]$target.id)
        if (-not (Test-RecordedIdentity $target $actual) -or -not (Test-BridgeArguments $actual)) { continue }
        if ($actual.id -ne [int]$LauncherRecord.id -and $actual.parent -ne [int]$LauncherRecord.id) { continue }
        $ownedProcess = [Diagnostics.Process]::GetProcessById($actual.id)
        try {
            $null = $ownedProcess.Handle
            $again = Get-ProcessIdentity $actual.id
            if (-not (Test-RecordedIdentity $target $again) -or -not (Test-BridgeArguments $again)) { continue }
            $ownedProcess.Kill()
            $null = $ownedProcess.WaitForExit(5000)
        } finally { $ownedProcess.Dispose() }
    }
}

function Test-BackgroundArguments($Identity, [string]$ExpectedRunId) {
    if (-not $Identity) { return $false }
    $tokens = @($Identity.arguments)
    return ($tokens.Count -eq 12 -and $tokens[1] -ieq '-NoLogo' -and $tokens[2] -ieq '-NoProfile' -and
        $tokens[3] -ieq '-NonInteractive' -and $tokens[4] -ieq '-ExecutionPolicy' -and $tokens[5] -ieq 'Bypass' -and
        $tokens[6] -ieq '-File' -and $tokens[7] -ieq $managerPath -and $tokens[8] -ieq '-Action' -and
        $tokens[9] -ieq 'run' -and $tokens[10] -ieq '-RunId' -and $tokens[11] -ceq $ExpectedRunId)
}

function Read-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try {
        $value = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json
        if ($value.schema -ne 1 -or $value.project -ine $projectRoot -or $value.run_id -notmatch '^[a-f0-9]{32}$') {
            throw 'Invalid state'
        }
        return $value
    } catch { throw 'Bridge state is invalid; refusing to select any process.' }
}

function Write-State($Value) {
    $temporary = Join-Path $stateRoot ('bridge-process.' + $Value.run_id + '.tmp')
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    # PowerShell converts $null to an empty string for this overload; use a true
    # null string so .NET does not treat the backup path as an invalid empty path.
    if (Test-Path -LiteralPath $statePath) { [IO.File]::Replace($temporary, $statePath, [NullString]::Value) }
    else { [IO.File]::Move($temporary, $statePath) }
}

function Get-VerifiedState {
    $saved = Read-State
    if (-not $saved) { return [pscustomobject]@{ status = 'stopped'; saved = $null; manager = $null; child = $null; workers = @() } }
    $manager = Get-ProcessIdentity ([int]$saved.manager.id)
    $managerValid = Test-RecordedIdentity $saved.manager $manager
    if ($managerValid -and $saved.background) { $managerValid = Test-BackgroundArguments $manager $saved.run_id }
    $child = $null
    if ($saved.child) { $child = Get-ProcessIdentity ([int]$saved.child.id) }
    $childValid = (Test-RecordedIdentity $saved.child $child) -and (Test-BridgeArguments $child)
    if ($childValid -and $child.parent -ne [int]$saved.manager.id) { $childValid = $false }
    $workers = @()
    foreach ($worker in @($saved.workers)) {
        $actual = Get-ProcessIdentity ([int]$worker.id)
        if ((Test-RecordedIdentity $worker $actual) -and (Test-BridgeArguments $actual) -and $actual.parent -eq [int]$saved.child.id) { $workers += $actual }
    }
    if ($childValid -or $workers.Count) { $label = 'running' }
    elseif ($managerValid -and $saved.status -eq 'starting') { $label = 'starting' }
    elseif ($saved.status -eq 'failed') { $label = 'failed' }
    else { $label = 'stopped' }
    return [pscustomobject]@{ status = $label; saved = $saved;
        manager = $(if ($managerValid) { $manager } else { $null }); child = $(if ($childValid) { $child } else { $null }); workers = $workers }
}

function Show-Status($Verified) {
    $result = [ordered]@{ status = $Verified.status; project = $projectRoot;
        process_verified = [bool]($Verified.child -or $Verified.workers.Count); manager_verified = [bool]$Verified.manager;
        note = 'Process status only; this does not prove QQ connectivity or delivery. Use scripts/manage-bridge.ps1 doctor for DPAPI-aware diagnostics, and scripts/run.ps1 status for database counts.' }
    if ($Verified.saved) {
        $result.run_id = $Verified.saved.run_id
        $result.bridge_pid = $(if ($Verified.child) { $Verified.child.id } else { $null })
        $result.interpreter_pids = @($Verified.workers | ForEach-Object { $_.id })
        $result.logs = @($Verified.saved.stdout, $Verified.saved.stderr)
        $result.exit_code = $Verified.saved.exit_code
    }
    $result | ConvertTo-Json -Depth 4
}

function Stop-VerifiedBridge($Verified) {
    if ($Verified.status -notin @('running', 'starting')) { return }
    # The marker is scoped to a verified run. Foreground run never kills the caller's shell.
    $marker = Join-Path $stateRoot ('bridge-stop-' + $Verified.saved.run_id + '.request')
    if ($Verified.manager) { [IO.File]::WriteAllText($marker, 'stop') }
    $until = [DateTime]::UtcNow.AddSeconds(12)
    do {
        $current = Get-VerifiedState
        if (-not $current.child -and -not $current.workers.Count -and $current.status -ne 'starting') { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $until)
    # Fallback targets only the exact original PID + creation time + executable + args.
    $current = Get-VerifiedState
    if ($current.saved.run_id -cne $Verified.saved.run_id) { throw 'Run changed while stopping; no process was terminated.' }
    if ($current.saved.child) { Stop-OwnedBridge $current.saved.child $current.saved.workers }
    # Do not kill a PowerShell wrapper: it could be the user's foreground shell.
    # The wrapper normally observes the child's exit and releases its own lease.
}

function Invoke-BridgeRun {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or -not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw 'The project .venv and config/local.json are required.'
    }
    $background = -not [string]::IsNullOrEmpty($RunId)
    $instanceId = $(if ($background) { $RunId } else { [Guid]::NewGuid().ToString('N') })
    [IO.Directory]::CreateDirectory($stateRoot) | Out-Null
    [IO.Directory]::CreateDirectory($logRoot) | Out-Null
    # One wrapper owns this file handle for its entire lifetime. No other Python is touched.
    $lease = [IO.File]::Open($leasePath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    $child = $null; $stdoutFile = $null; $stderrFile = $null; $stdoutCopy = $null; $stderrCopy = $null
    $secure = $null; $plain = $null; $bstr = [IntPtr]::Zero
    $marker = Join-Path $stateRoot ('bridge-stop-' + $instanceId + '.request')
    $record = [ordered]@{ schema = 1; project = $projectRoot; run_id = $instanceId; background = $background;
        status = 'starting'; manager = Get-IdentityRecord (Get-ProcessIdentity $PID); child = $null; workers = @();
        stdout = Join-Path $logRoot ('bridge-' + $instanceId + '.stdout.log');
        stderr = Join-Path $logRoot ('bridge-' + $instanceId + '.stderr.log'); exit_code = $null }
    $failure = $false
    Push-Location -LiteralPath $projectRoot
    try {
        Write-State $record
        if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { throw 'Missing DPAPI secret file' }
        $secure = [IO.File]::ReadAllText($secretPath).Trim() | ConvertTo-SecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ($plain.Length -lt 24 -or $plain.Length -gt 4096 -or $plain -match '\s|[^\x00-\x7F]') { throw 'Invalid token format' }
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = $pythonPath
        $info.Arguments = '-m qq_character --config "' + $configPath + '" serve'
        $info.WorkingDirectory = $projectRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        # This is the child's environment only; never set $env:QQ_CHARACTER_ONEBOT_TOKEN.
        $info.EnvironmentVariables['QQ_CHARACTER_ONEBOT_TOKEN'] = $plain
        $info.EnvironmentVariables['PYTHONPATH'] = Join-Path $projectRoot 'src'
        $info.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'
        $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        $stdoutFile = [IO.FileStream]::new($record.stdout, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1, [IO.FileOptions]::Asynchronous)
        $stderrFile = [IO.FileStream]::new($record.stderr, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read, 1, [IO.FileOptions]::Asynchronous)
        $child = New-Object Diagnostics.Process
        $child.StartInfo = $info
        if (-not $child.Start()) { throw 'Bridge process did not start' }
        $info.EnvironmentVariables.Remove('QQ_CHARACTER_ONEBOT_TOKEN')
        $plain = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $bstr = [IntPtr]::Zero
        $secure.Dispose(); $secure = $null
        $stdoutCopy = $child.StandardOutput.BaseStream.CopyToAsync($stdoutFile)
        $stderrCopy = $child.StandardError.BaseStream.CopyToAsync($stderrFile)
        $identity = Get-ProcessIdentity $child.Id
        if (-not (Test-BridgeArguments $identity) -or $identity.parent -ne $PID) { throw 'Started child identity could not be verified' }
        $record.child = Get-IdentityRecord $identity
        $record.workers = @((Get-BridgeWorkers $identity) | ForEach-Object { Get-IdentityRecord $_ })
        $record.status = 'running'
        Write-State $record
        if (-not $background) { Write-Output 'Foreground bridge running. Logs stay in .state/logs. Use manage-bridge.ps1 stop from another terminal.' }
        while (-not $child.WaitForExit(250)) {
            if (Test-Path -LiteralPath $marker -PathType Leaf) {
                # Windows force-stop: the next bridge start recovers in-flight delivery as uncertain.
                Stop-OwnedBridge $record.child $record.workers
                $null = $child.WaitForExit(5000)
                break
            }
        }
        $record.exit_code = $child.ExitCode
        $record.status = $(if ((Test-Path -LiteralPath $marker) -or $record.exit_code -eq 0) { 'stopped' } else { 'failed' })
    } catch {
        $failure = $true
        $record.status = 'failed'
        # Exception messages may contain runtime data. Only a generic failure is exposed.
        Write-Warning 'Bridge wrapper failed. Check local configuration, DPAPI ownership and project logs; no credential value is displayed.'
    } finally {
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        $plain = $null
        if ($secure) { $secure.Dispose() }
        if ($child) {
            if ($record.child) { Stop-OwnedBridge $record.child $record.workers }
            elseif (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(5000) }
            foreach ($copy in @($stdoutCopy, $stderrCopy)) { if ($copy) { try { $null = $copy.Wait(3000) } catch {} } }
            if ($child.HasExited) { $record.exit_code = $child.ExitCode }
            $child.Dispose()
        }
        if ($stdoutFile) { $stdoutFile.Dispose() }
        if ($stderrFile) { $stderrFile.Dispose() }
        if ($record.status -eq 'running') { $record.status = 'stopped' }
        Write-State $record
        if (Test-Path -LiteralPath $marker) { [IO.File]::Delete($marker) }
        $lease.Dispose()
        Pop-Location
    }
    if ($failure -or $record.status -eq 'failed') { throw 'Bridge could not run; inspect local diagnostics.' }
}

function Test-DoctorArguments($Identity) {
    if (-not $Identity -or -not ($approvedPythonPaths -icontains $Identity.path)) { return $false }
    $tokens = @($Identity.arguments)
    return ($tokens.Count -eq 6 -and $tokens[1] -ceq '-m' -and $tokens[2] -ceq 'qq_character' -and
        $tokens[3] -ceq '--config' -and $tokens[4] -ieq $configPath -and $tokens[5] -ceq 'doctor')
}

function Invoke-BridgeDoctor {
    # No bridge lease, process-state, log file or service is created by this action.
    $script:DoctorExitCode = 1
    $secure = $null; $plain = $null; $bstr = [IntPtr]::Zero
    $diagnostic = $null; $diagnosticIdentity = $null; $workerRecords = @(); $started = $false
    $info = $null; $stderrDrain = $null
    try {
        foreach ($required in @($pythonPath, $configPath, $secretPath)) {
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'Missing doctor input' }
        }
        $secure = [IO.File]::ReadAllText($secretPath).Trim() | ConvertTo-SecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if ($plain.Length -lt 24 -or $plain.Length -gt 4096 -or $plain -match '\s|[^\x00-\x7F]') { throw 'Invalid token format' }
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = $pythonPath
        $info.Arguments = '-m qq_character --config "' + $configPath + '" doctor'
        $info.WorkingDirectory = $projectRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        # Identical credential boundary to run: only this child receives the DPAPI value.
        $info.EnvironmentVariables['QQ_CHARACTER_ONEBOT_TOKEN'] = $plain
        $info.EnvironmentVariables['PYTHONPATH'] = Join-Path $projectRoot 'src'
        $info.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'
        $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        $diagnostic = New-Object Diagnostics.Process
        $diagnostic.StartInfo = $info
        $started = $diagnostic.Start()
        if (-not $started) { throw 'Doctor process did not start' }
        $info.EnvironmentVariables.Remove('QQ_CHARACTER_ONEBOT_TOKEN')
        $plain = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); $bstr = [IntPtr]::Zero
        $secure.Dispose(); $secure = $null
        $stdoutRead = $diagnostic.StandardOutput.ReadToEndAsync()
        # Never relay raw stderr or exception messages that might contain runtime data.
        $stderrDrain = $diagnostic.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $diagnosticIdentity = Get-ProcessIdentity $diagnostic.Id
        if ($diagnosticIdentity) {
            if (-not (Test-DoctorArguments $diagnosticIdentity) -or $diagnosticIdentity.parent -ne $PID) { throw 'Doctor identity mismatch' }
            foreach ($item in @(Get-CimInstance -ClassName Win32_Process -Filter "ParentProcessId = $($diagnostic.Id)")) {
                $worker = Get-ProcessIdentity ([int]$item.ProcessId)
                if ($worker -and (Test-DoctorArguments $worker)) { $workerRecords += Get-IdentityRecord $worker }
            }
        }
        if (-not $diagnostic.WaitForExit(90000)) { throw 'Doctor deadline exceeded' }
        $output = $stdoutRead.GetAwaiter().GetResult()
        if ($output.Length -gt 1048576) { throw 'Doctor output exceeds limit' }
        $null = $output | ConvertFrom-Json
        # The existing CLI supplies its redacted JSON. Preserve its exit code (0/2/etc.).
        Write-Output $output.Trim()
        $script:DoctorExitCode = $diagnostic.ExitCode
    } catch {
        [ordered]@{ bridge_ready = $false; doctor_wrapper_error = 'Doctor could not complete. Check the project environment, local configuration and DPAPI file ownership.';
            token_value_displayed = $false } | ConvertTo-Json
    } finally {
        if ($info) { $info.EnvironmentVariables.Remove('QQ_CHARACTER_ONEBOT_TOKEN') }
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        $plain = $null
        if ($secure) { $secure.Dispose() }
        if ($diagnostic) {
            if ($started -and -not $diagnostic.HasExited) {
                # Handle the Windows venv redirector without changing bridge stop rules.
                $currentLauncher = Get-ProcessIdentity $diagnostic.Id
                if ($diagnosticIdentity -and (Test-RecordedIdentity $diagnosticIdentity $currentLauncher)) {
                    foreach ($item in @(Get-CimInstance -ClassName Win32_Process -Filter "ParentProcessId = $($diagnostic.Id)")) {
                        $worker = Get-ProcessIdentity ([int]$item.ProcessId)
                        if ($worker -and (Test-DoctorArguments $worker)) { $workerRecords += Get-IdentityRecord $worker }
                    }
                }
                foreach ($recorded in $workerRecords) {
                    $actual = Get-ProcessIdentity ([int]$recorded.id)
                    if (-not (Test-RecordedIdentity $recorded $actual) -or -not (Test-DoctorArguments $actual) -or $actual.parent -ne $diagnostic.Id) { continue }
                    $ownedWorker = [Diagnostics.Process]::GetProcessById($actual.id)
                    try {
                        $null = $ownedWorker.Handle
                        if (Test-RecordedIdentity $recorded (Get-ProcessIdentity $actual.id)) { $ownedWorker.Kill(); $null = $ownedWorker.WaitForExit(5000) }
                    } finally { $ownedWorker.Dispose() }
                }
                if (-not $diagnostic.HasExited) { $diagnostic.Kill(); $null = $diagnostic.WaitForExit(5000) }
            }
            if ($stderrDrain) { try { $null = $stderrDrain.Wait(3000) } catch {} }
            $diagnostic.Dispose()
        }
    }
}

if ($Action -eq 'run') {
    Invoke-BridgeRun
    return
}
if ($RunId) { throw '-RunId is reserved for the run entry point.' }
if ($Action -eq 'doctor') {
    Invoke-BridgeDoctor
    exit $script:DoctorExitCode
}
if ($Action -eq 'status') {
    Show-Status (Get-VerifiedState)
    return
}

$mutex = New-Object Threading.Mutex($false, ('Local\QQCharacterBridge-' + (Get-TextHash $projectRoot.ToLowerInvariant())))
$held = $false
try {
    try { $held = $mutex.WaitOne(10000) } catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw 'Another management command is still running.' }
    $verified = Get-VerifiedState
    if ($Action -eq 'stop') {
        Stop-VerifiedBridge $verified
        Show-Status (Get-VerifiedState)
    } elseif ($verified.status -in @('running', 'starting')) {
        Show-Status $verified
    } else {
        foreach ($required in @($pythonPath, $configPath, $secretPath)) {
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'The .venv, local configuration and .state/secrets/onebot.dpapi must exist.' }
        }
        [IO.Directory]::CreateDirectory($stateRoot) | Out-Null
        [IO.Directory]::CreateDirectory($logRoot) | Out-Null
        $newRunId = [Guid]::NewGuid().ToString('N')
        $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $managerPath + '" -Action run -RunId ' + $newRunId
        $launcherOutput = Join-Path $logRoot ('manager-' + $newRunId + '.stdout.log')
        $launcherError = Join-Path $logRoot ('manager-' + $newRunId + '.stderr.log')
        $launcher = Start-Process -FilePath $managerExecutable -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $launcherOutput -RedirectStandardError $launcherError
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            do {
                Start-Sleep -Milliseconds 250
                $current = Get-VerifiedState
                if ($current.saved -and $current.saved.run_id -ceq $newRunId -and $current.status -eq 'running') {
                    # Surviving this short grace period is process proof, not a service health check.
                    Start-Sleep -Seconds 1
                    $current = Get-VerifiedState
                    if ($current.status -eq 'running') { Show-Status $current; return }
                }
                $launcher.Refresh()
                if ($launcher.HasExited) { throw 'Bridge wrapper exited during startup; inspect .state/logs and run doctor.' }
            } while ([DateTime]::UtcNow -lt $deadline)
            throw 'Startup is not confirmed. Run status before retrying; no process was killed.'
        } finally { $launcher.Dispose() }
    }
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
