<#
.SYNOPSIS
Manage only the dedicated NapCat WebUI at 127.0.0.1:6098.
.DESCRIPTION
Reads current-user DPAPI secrets from .state/secrets. Never accepts secrets on
the command line and never prints response bodies, credentials, or QR URLs.
status -WaitSeconds 180 reuses one WebUI login while waiting for QQ readiness;
separate invocations authenticate again and count toward the WebUI login limit.
qr reads the current QR payload; only qr-refresh explicitly requests a refresh.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-napcat.ps1 status
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/manage-napcat.ps1 status -WaitSeconds 180
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'qr', 'qr-refresh', 'verify-config')]
    [string]$Action = 'status',

    [ValidateRange(0, 180)]
    [int]$WaitSeconds = 0
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:NapcatBaseUri = 'http://127.0.0.1:6098'
$script:NapcatCredential = $null
$script:NapcatHttp = $null
$taskHandler = $null
$taskWebuiToken = $null
$taskOnebotToken = $null
$taskStage = 'initialization'
$taskExitCode = 1
$taskProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskStateRoot = Join-Path $taskProjectRoot '.state'
$taskSecretsRoot = Join-Path $taskStateRoot 'secrets'

function Assert-TaskPath {
    param([string]$Path, [bool]$MustExist = $true)
    if (-not (Test-Path -LiteralPath $Path)) {
        if ($MustExist) { throw [IO.FileNotFoundException]::new('Required local file is missing.') }
        return
    }
    $taskItem = Get-Item -LiteralPath $Path -Force
    if (($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw [IO.IOException]::new('Linked state paths are not accepted.')
    }
}

function Read-TaskSecret {
    param([ValidateSet('napcat-webui.dpapi', 'onebot.dpapi')][string]$Name)
    Assert-TaskPath $taskStateRoot
    Assert-TaskPath $taskSecretsRoot
    $taskSecretPath = Join-Path $taskSecretsRoot $Name
    Assert-TaskPath $taskSecretPath
    $taskFile = Get-Item -LiteralPath $taskSecretPath -Force
    if ($taskFile.PSIsContainer -or $taskFile.Length -gt 32768) {
        throw [IO.IOException]::new('Invalid secret file.')
    }
    $taskEncrypted = [IO.File]::ReadAllText($taskSecretPath).Trim()
    $taskSecure = $null
    $taskPointer = [IntPtr]::Zero
    try {
        # No -Key: Windows DPAPI binds this file to its creating user/context.
        $taskSecure = ConvertTo-SecureString -String $taskEncrypted -ErrorAction Stop
        $taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecure)
        $taskPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer)
        if ($taskPlain.Length -lt 24 -or $taskPlain.Length -gt 4096 -or $taskPlain -notmatch '^[\x21-\x7e]+$') {
            throw [Security.SecurityException]::new('Invalid secret format.')
        }
        return $taskPlain
    }
    finally {
        if ($taskPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer)
        }
        if ($null -ne $taskSecure) { $taskSecure.Dispose() }
        $taskEncrypted = $null
        $taskPlain = $null
    }
}

function Invoke-NapcatApi {
    param(
        [ValidateSet('/auth/login', '/QQLogin/CheckLoginStatus', '/QQLogin/GetQQLoginInfo',
                     '/QQLogin/GetQQLoginQrcode', '/QQLogin/RefreshQRcode', '/OB11Config/GetConfig')]
        [string]$Path,
        [hashtable]$Body = @{}
    )
    $taskRequest = $null
    $taskResponse = $null
    try {
        $taskRequest = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, $script:NapcatBaseUri + '/api' + $Path)
        $taskJson = ConvertTo-Json -InputObject $Body -Compress -Depth 8
        $taskRequest.Content = [Net.Http.StringContent]::new($taskJson, [Text.Encoding]::UTF8, 'application/json')
        if ($Path -ne '/auth/login') {
            if ([string]::IsNullOrEmpty($script:NapcatCredential)) {
                throw [Security.SecurityException]::new('WebUI authentication required.')
            }
            $taskRequest.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $script:NapcatCredential)
        }
        $taskResponse = $script:NapcatHttp.SendAsync($taskRequest).GetAwaiter().GetResult()
        if (-not $taskResponse.IsSuccessStatusCode) {
            throw [Net.Http.HttpRequestException]::new('Local management request failed.')
        }
        $taskText = $taskResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($taskText.Length -gt 262144) { throw [IO.IOException]::new('Management response is oversized.') }
        $taskReply = ConvertFrom-Json -InputObject $taskText -ErrorAction Stop
        if ($null -eq $taskReply.code -or $taskReply.code -ne 0) {
            throw [InvalidOperationException]::new('Local management API rejected the request.')
        }
        return $taskReply.data
    }
    finally {
        if ($null -ne $taskResponse) { $taskResponse.Dispose() }
        if ($null -ne $taskRequest) { $taskRequest.Dispose() }
        $taskJson = $null
        $taskText = $null
        $taskReply = $null
    }
}

function Test-TaskTrue {
    param($Value)
    return ($Value -is [bool] -and $Value)
}

function Save-TaskQr {
    param($Value)
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or
        $Value.Length -gt 8192 -or $Value -match '[\x00\r\n]') {
        throw [InvalidOperationException]::new('QR payload is unavailable or invalid.')
    }
    $taskQrUri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$taskQrUri) -or
        $taskQrUri.Scheme -notin @('https', 'http')) {
        throw [InvalidOperationException]::new('QR payload is not an expected URL.')
    }
    Assert-TaskPath $taskStateRoot
    $taskDestination = Join-Path $taskStateRoot 'napcat-qr-url.txt'
    Assert-TaskPath $taskDestination $false
    $taskTemporary = Join-Path $taskStateRoot ('napcat-qr-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($taskTemporary, $Value, [Text.UTF8Encoding]::new($false))
        # The URL is login material. Restrict this newly created file to its owner.
        $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $taskAcl = [Security.AccessControl.FileSecurity]::new()
        $taskAcl.SetOwner($taskIdentity)
        $taskAcl.SetAccessRuleProtection($true, $false)
        $taskRule = [Security.AccessControl.FileSystemAccessRule]::new(
            $taskIdentity, [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow)
        $taskAcl.AddAccessRule($taskRule)
        Set-Acl -LiteralPath $taskTemporary -AclObject $taskAcl -ErrorAction Stop
        Move-Item -LiteralPath $taskTemporary -Destination $taskDestination -Force
    }
    finally {
        # Exact, task-created file only; no recursive deletion or computed directory removal.
        if (Test-Path -LiteralPath $taskTemporary) { Remove-Item -LiteralPath $taskTemporary -Force }
    }
}

try {
    if ($Action -ne 'status' -and $WaitSeconds -ne 0) {
        throw [ArgumentException]::new('Waiting is only available for status.')
    }
    Add-Type -AssemblyName System.Net.Http -ErrorAction Stop
    $taskHandler = [Net.Http.HttpClientHandler]::new()
    $taskHandler.UseProxy = $false
    $taskHandler.AllowAutoRedirect = $false
    $taskHandler.UseCookies = $false
    $script:NapcatHttp = [Net.Http.HttpClient]::new($taskHandler)
    $script:NapcatHttp.Timeout = [TimeSpan]::FromSeconds(20)
    $script:NapcatHttp.MaxResponseContentBufferSize = 262144

    $taskStage = 'read_webui_secret'
    $taskWebuiToken = Read-TaskSecret 'napcat-webui.dpapi'
    $taskHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $taskBytes = [Text.Encoding]::UTF8.GetBytes($taskWebuiToken + '.napcat')
        $taskHashBytes = $taskHasher.ComputeHash($taskBytes)
        $taskHash = [BitConverter]::ToString($taskHashBytes).Replace('-', '').ToLowerInvariant()
    }
    finally {
        if ($null -ne $taskBytes) { [Array]::Clear($taskBytes, 0, $taskBytes.Length) }
        if ($null -ne $taskHashBytes) { [Array]::Clear($taskHashBytes, 0, $taskHashBytes.Length) }
        $taskHasher.Dispose()
        $taskWebuiToken = $null
    }
    $taskStage = 'authenticate'
    $taskAuthentication = Invoke-NapcatApi '/auth/login' @{ hash = $taskHash }
    $taskHash = $null
    if (Test-TaskTrue $taskAuthentication.require2FA) {
        $taskStage = 'webui_two_factor_required'
        throw [Security.SecurityException]::new('Interactive second factor is required.')
    }
    $script:NapcatCredential = $taskAuthentication.Credential
    if ($script:NapcatCredential -isnot [string] -or $script:NapcatCredential.Length -gt 8192 -or
        $script:NapcatCredential -notmatch '^[A-Za-z0-9+/]+={0,2}$') {
        throw [Security.SecurityException]::new('Invalid WebUI credential response.')
    }
    $taskAuthentication = $null

    if ($Action -eq 'status') {
        $taskStage = 'read_status'
        $taskDeadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
        do {
            $taskStatus = Invoke-NapcatApi '/QQLogin/CheckLoginStatus'
            $taskReady = (Test-TaskTrue $taskStatus.isLogin) -and (Test-TaskTrue $taskStatus.coreReady)
            if ($taskReady -or [DateTime]::UtcNow -ge $taskDeadline) { break }
            Start-Sleep -Seconds 3
        } while ($true)
        $taskPhase = 'unknown'
        if ($taskStatus.loginPhase -in @('waiting_qrcode', 'generating_qrcode', 'qrcode_scanned',
                                         'initializing', 'ready', 'offline', 'reconnecting')) {
            $taskPhase = $taskStatus.loginPhase
        }
        $taskUin = $null
        $taskOnline = $null
        if ($taskReady) {
            $taskStage = 'read_login_identity'
            $taskInfo = Invoke-NapcatApi '/QQLogin/GetQQLoginInfo'
            if ([string]$taskInfo.uin -notmatch '^[1-9][0-9]{0,19}$') {
                throw [InvalidOperationException]::new('Invalid logged-in account identifier.')
            }
            $taskUin = [string]$taskInfo.uin
            $taskOnline = Test-TaskTrue $taskInfo.online
        }
        $taskSummary = [ordered]@{
            ok = $true; action = $Action; isLogin = (Test-TaskTrue $taskStatus.isLogin)
            coreReady = (Test-TaskTrue $taskStatus.coreReady); isOffline = (Test-TaskTrue $taskStatus.isOffline)
            loginPhase = $taskPhase; uin = $taskUin; online = $taskOnline
        }
    }
    elseif ($Action -in @('qr', 'qr-refresh')) {
        $taskStage = 'read_qr'
        if ($Action -eq 'qr') {
            $taskQr = Invoke-NapcatApi '/QQLogin/GetQQLoginQrcode'
            $taskQrPayload = $taskQr.qrcode
        }
        else {
            $taskQr = Invoke-NapcatApi '/QQLogin/RefreshQRcode'
            if (Test-TaskTrue $taskQr.restarting) {
                $taskStage = 'qq_login_service_restarting'
                throw [InvalidOperationException]::new('Wait for login service recovery.')
            }
            $taskQrPayload = $taskQr.qrcodeurl
        }
        $taskStage = 'write_qr_file'
        Save-TaskQr $taskQrPayload
        $taskSummary = [ordered]@{
            ok = $true; action = $Action; saved = $true
            file = '.state/napcat-qr-url.txt'; url_displayed = $false
            refreshed = ($Action -eq 'qr-refresh'); saved_at_utc = [DateTime]::UtcNow.ToString('o')
        }
        $taskQrPayload = $null
        $taskQr = $null
    }
    elseif ($Action -eq 'verify-config') {
        $taskStage = 'read_onebot_secret'
        $taskOnebotToken = Read-TaskSecret 'onebot.dpapi'
        $taskStage = 'read_onebot_config'
        $taskConfiguration = Invoke-NapcatApi '/OB11Config/GetConfig'
        $taskClients = @($taskConfiguration.network.websocketClients | Where-Object { $null -ne $_ })
        $taskExpectedClients = @($taskClients | Where-Object { $_.name -ceq 'qq-character' })
        $taskClient = if ($taskExpectedClients.Count -eq 1) { $taskExpectedClients[0] } else { $null }
        $taskExpectedUrl = 'ws://host.docker.internal:6198/onebot'
        $taskUrlMatch = $null -ne $taskClient -and $taskClient.url -ceq $taskExpectedUrl
        $taskTokenMatch = $null -ne $taskClient -and $taskClient.token -is [string] -and $taskClient.token -ceq $taskOnebotToken
        $taskArrayFormat = $null -ne $taskClient -and $taskClient.messagePostFormat -ceq 'array'
        $taskSelfReportOff = $null -ne $taskClient -and $taskClient.reportSelfMessage -is [bool] -and -not $taskClient.reportSelfMessage
        $taskDebugOff = $null -ne $taskClient -and $taskClient.debug -is [bool] -and -not $taskClient.debug
        $taskEnabled = $null -ne $taskClient -and (Test-TaskTrue $taskClient.enable)
        $taskOtherCount = 0
        foreach ($taskKind in @('httpServers', 'httpSseServers', 'httpClients', 'websocketServers', 'plugins')) {
            $taskOtherCount += @($taskConfiguration.network.$taskKind | Where-Object { $null -ne $_ }).Count
        }
        $taskPassed = $taskClients.Count -eq 1 -and $taskExpectedClients.Count -eq 1 -and
            $taskUrlMatch -and $taskTokenMatch -and $taskArrayFormat -and $taskSelfReportOff -and
            $taskDebugOff -and $taskEnabled -and $taskOtherCount -eq 0
        $taskSummary = [ordered]@{
            ok = $true; action = $Action; passed = $taskPassed
            websocket_client_count = $taskClients.Count; expected_name_found = ($taskExpectedClients.Count -eq 1)
            enabled = $taskEnabled; token_match = $taskTokenMatch; url_match = $taskUrlMatch
            url = $(if ($taskUrlMatch) { $taskExpectedUrl } else { '<unexpected-or-missing>' })
            message_format_array = $taskArrayFormat; self_report_disabled = $taskSelfReportOff
            debug_disabled = $taskDebugOff; other_adapter_count = $taskOtherCount
            live_connection_verified = $false
        }
        $taskConfiguration = $null
        $taskClient = $null
        $taskClients = $null
        $taskExpectedClients = $null
        $taskOnebotToken = $null
    }
    $taskSummary | ConvertTo-Json -Depth 4 -Compress
    $taskExitCode = if ($Action -eq 'verify-config' -and -not $taskPassed) { 2 } else { 0 }
}
catch {
    # Never emit exception messages: HttpClient/server errors can contain secrets.
    [ordered]@{ ok = $false; action = $Action; error_stage = $taskStage; sensitive_output = $false } |
        ConvertTo-Json -Compress
    $taskExitCode = 1
}
finally {
    if ($null -ne $script:NapcatHttp) { $script:NapcatHttp.Dispose() }
    if ($null -ne $taskHandler) { $taskHandler.Dispose() }
    $script:NapcatCredential = $null
    $taskWebuiToken = $null
    $taskOnebotToken = $null
    $taskHash = $null
    $taskAuthentication = $null
}
exit $taskExitCode
