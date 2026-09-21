param(
    [ValidateSet('LAN', 'LocalOnly')]
    [string]$Mode = 'LAN',

    [ValidateRange(1, 65535)]
    [int]$Port = 8080,

    # Compatibility with existing manual invocations of run.ps1 -Bind ... .
    # Only the two audited bindings are supported: no arbitrary interfaces.
    [string]$Bind = '',

    # Read-only inspection: no venv, token, socket, or database access.
    [switch]$ShowNetworkConfig
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# A previously documented `-Bind` command is still accepted, but must never
# contradict an explicitly supplied `-Mode` argument.
if ($PSBoundParameters.ContainsKey('Bind')) {
    $legacyMode = switch ($Bind) {
        '0.0.0.0' { 'LAN' }
        '127.0.0.1' { 'LocalOnly' }
        default { throw "Unsupported -Bind '$Bind'. Use -Mode LAN or -Mode LocalOnly." }
    }
    if ($PSBoundParameters.ContainsKey('Mode') -and $Mode -ne $legacyMode) {
        throw '-Bind contradicts -Mode. Provide only one or use matching values.'
    }
    $Mode = $legacyMode
}

$listenAddress = if ($Mode -eq 'LAN') { '0.0.0.0' } else { '127.0.0.1' }
if ($ShowNetworkConfig) {
    Write-Host "Gateway network mode: $Mode | listening on ${listenAddress}:$Port"
    Write-Host "Gateway UI: http://127.0.0.1:$Port"
    exit 0
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Run .\setup.ps1 first.'
}

# Preserve the established encrypted-at-rest, per-Windows-user gateway token.
# Never print the plaintext token, put it in the URL, or save it to this script.
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

Write-Host "Gateway network mode: $Mode | listening on ${listenAddress}:$Port"
if ($Mode -eq 'LAN') {
    Write-Host 'LAN mode: listens on all IPv4 interfaces. Windows Firewall/network controls determine who can connect.'
    Write-Host 'Keep TCP 8080 off your router port-forwarding rules; use a trusted Private network or TLS/VPN when needed.'
} else {
    Write-Host 'LocalOnly mode: other computers and their Docker containers cannot connect to this gateway.'
}

# Preserve Pinokio's existing URL-capture contract. The UI URL is local even
# when the listener is LAN-accessible; remote n8n uses the gateway hostname.
Write-Host "Gateway UI: http://127.0.0.1:$Port"
& $venvPython -m uvicorn gateway.app:app --host $listenAddress --port $Port --no-access-log
exit $LASTEXITCODE
