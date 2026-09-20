$ErrorActionPreference = 'Stop'
$tokenPath = Join-Path $PSScriptRoot '.gateway-token'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    throw 'No saved gateway token. Run .\run.ps1 once without AI_GATEWAY_TOKEN set.'
}
$secureToken = Get-Content -Raw -LiteralPath $tokenPath | ConvertTo-SecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
