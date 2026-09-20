param(
    [string]$Bind = '127.0.0.1',
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Run .\setup.ps1 first.'
}
$tokenPath = Join-Path $PSScriptRoot '.gateway-token'
$usingSavedToken = [string]::IsNullOrWhiteSpace($env:AI_GATEWAY_TOKEN)
if ($usingSavedToken) {
    if (Test-Path -LiteralPath $tokenPath) {
        $secureToken = Get-Content -Raw -LiteralPath $tokenPath | ConvertTo-SecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try {
            $env:AI_GATEWAY_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    } else {
        $env:AI_GATEWAY_TOKEN = & $venvPython -c 'import secrets; print(secrets.token_urlsafe(32))'
        $encrypted = ConvertTo-SecureString -String $env:AI_GATEWAY_TOKEN -AsPlainText -Force | ConvertFrom-SecureString
        [IO.File]::WriteAllText($tokenPath, $encrypted, [Text.UTF8Encoding]::new($false))
        Write-Host 'Created an encrypted gateway token for this Windows user.'
    }
}
if ($env:AI_GATEWAY_TOKEN.Length -lt 24) {
    throw 'AI_GATEWAY_TOKEN must have at least 24 characters.'
}

if ($usingSavedToken) {
    Write-Host 'Use .\show-token.ps1 in another PowerShell window to copy the token into n8n.'
} else {
    Write-Host 'AI_GATEWAY_TOKEN override is active; use that value in n8n.'
}
Write-Host "Gateway UI: http://127.0.0.1:$Port"
& $venvPython -m uvicorn gateway.app:app --host $Bind --port $Port --no-access-log
