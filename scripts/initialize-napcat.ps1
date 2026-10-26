# First deployment only: seed new named volumes without putting tokens in argv/env/logs.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configPath = Join-Path $projectRoot 'config\local.json'
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$docker = $config.runtime.docker_command
$env:PATH = (Split-Path -Parent $docker) + ';' + $env:PATH
$compose = Join-Path $projectRoot 'docker\napcat.compose.yaml'
$secretsDir = Join-Path $projectRoot '.state\secrets'
$seedDir = Join-Path $projectRoot '.runtime\napcat-seed'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Invoke-CharacterDocker {
    param([string[]]$DockerArguments)
    & $docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated NapCat Docker operation failed.' }
}

function New-CharacterSecret {
    param([string]$Name)
    $path = Join-Path $secretsDir ($Name + '.dpapi')
    if (Test-Path -LiteralPath $path) { throw "Secret already exists: $Name; inspect deployment state before retrying." }
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $plain = [Convert]::ToBase64String($bytes)
    $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
    $protected = ConvertFrom-SecureString -SecureString $secure
    [IO.File]::WriteAllText($path, $protected, $utf8)
    return $plain
}

Push-Location -LiteralPath $projectRoot
try {
    $containers = & $docker ps -a --filter 'name=^/qq-character-napcat$' --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0 -or $containers) { throw 'Docker unavailable or dedicated NapCat already exists; no overwrite attempted.' }
    $existingVolumes = & $docker volume ls --format '{{.Name}}'
    if ($LASTEXITCODE -ne 0 -or ($existingVolumes | Where-Object { $_ -in @('qq-character-qq-data','qq-character-napcat-config','qq-character-napcat-plugins') })) { throw 'Docker unavailable or NapCat volumes already exist; inspect before retrying.' }
    New-Item -ItemType Directory -Path $secretsDir,$seedDir -Force | Out-Null
    $onebot = New-CharacterSecret 'onebot'
    $webui = New-CharacterSecret 'napcat-webui'
    $webConfig = @{host='0.0.0.0';port=6099;prefix='';token=$webui;loginRate=3}
    $onebotConfig = @{
        network=@{
            httpServers=@();httpSseServers=@();httpClients=@();websocketServers=@();plugins=@()
            websocketClients=@(@{enable=$true;name='qq-character';url='ws://host.docker.internal:6198/onebot';reportSelfMessage=$false;messagePostFormat='array';token=$onebot;debug=$false;heartInterval=30000;reconnectInterval=10000})
        }
        musicSignUrl='';enableLocalFile2Url=$false;parseMultMsg=$false
    }
    $webSeed = Join-Path $seedDir 'webui.json'
    $onebotSeed = Join-Path $seedDir 'onebot11.json'
    [IO.File]::WriteAllText($webSeed, ($webConfig | ConvertTo-Json -Depth 8), $utf8)
    [IO.File]::WriteAllText($onebotSeed, ($onebotConfig | ConvertTo-Json -Depth 8), $utf8)
    Invoke-CharacterDocker -DockerArguments @('compose','-f',$compose,'config','--quiet')
    Invoke-CharacterDocker -DockerArguments @('compose','-f',$compose,'create')
    Invoke-CharacterDocker -DockerArguments @('cp',$webSeed,'qq-character-napcat:/app/napcat/config/webui.json')
    Invoke-CharacterDocker -DockerArguments @('cp',$onebotSeed,'qq-character-napcat:/app/napcat/config/onebot11.json')
    Invoke-CharacterDocker -DockerArguments @('compose','-f',$compose,'start')
    Write-Output 'Dedicated NapCat started; local WebUI port 6098. Secrets protected with current-user DPAPI; no token displayed.'
} finally {
    foreach ($leaf in @('webui.json','onebot11.json')) {
        $seedPath = Join-Path $seedDir $leaf
        if (Test-Path -LiteralPath $seedPath) { Remove-Item -LiteralPath $seedPath -Force }
    }
    $onebot = $null; $webui = $null
    Pop-Location
}
