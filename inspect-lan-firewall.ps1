# Read-only Windows Firewall/network inspection for the Local AI Gateway.
# This script does not enable/disable a firewall or add/remove any rules.
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'
Write-Host '=== Local AI Gateway: read-only network inspection ==='
Write-Host "Gateway TCP port: $Port"
Write-Host ''
Write-Host 'Active network profiles:'
Get-NetConnectionProfile | Select-Object InterfaceAlias, NetworkCategory, IPv4Connectivity | Format-Table -AutoSize
Write-Host ''
Write-Host 'Windows Firewall profiles (enabled and default inbound action):'
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table -AutoSize
Write-Host ''
Write-Host "Local TCP listeners on port ${Port}:"
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess | Format-Table -AutoSize
Write-Host ''
Write-Host 'Dedicated Private-profile rule, if configured:'
$ruleName = "Local AI Gateway - Private LAN TCP $Port"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
    Select-Object DisplayName, Enabled, Profile, Direction, Action | Format-Table -AutoSize
Write-Host ''
Write-Host 'Important: other application-wide or port-wide inbound rules may also permit connections.'
Write-Host 'Check Windows Defender Firewall with Advanced Security (wf.msc) for broader existing rules.'
