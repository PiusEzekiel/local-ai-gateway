"""Module 9D: LAN-aware Pinokio launcher / network security contract.

The PowerShell integration check runs on Windows; static checks also run on
non-Windows dev hosts that do not have the Windows PowerShell executable.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
START = ROOT / 'start.js'
RUN = ROOT / 'app' / 'run.ps1'
FIREWALL = ROOT / 'inspect-lan-firewall.ps1'
GUIDE = ROOT / 'MODULE_9D_INSTALL.md'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def test_launcher_keeps_lan_mode_as_pinokio_default():
    src = read(START)
    assert 'run.ps1 -Mode LAN -Port 8080' in src
    assert 'daemon: true' in src
    assert 'method: "shell.run"' in src


def test_pinokio_local_url_handshake_is_preserved():
    src = read(START)
    assert 'event: "/(http:' in src
    assert 'method: "local.set"' in src
    assert 'url: "{{input.event[1]}}"' in src
    assert 'method: "process.wait"' in src
    assert 'uri: "{{local.url}}"' in src


def test_run_script_exposes_only_two_network_modes():
    src = read(RUN)
    assert "[ValidateSet('LAN', 'LocalOnly')]" in src
    assert "[string]$Mode = 'LAN'" in src
    assert "'0.0.0.0'" in src
    assert "'127.0.0.1'" in src
    assert 'else { \'127.0.0.1\' }' in src
    assert '-Mode LAN or -Mode LocalOnly' in src


def test_run_script_preserves_legacy_bind_without_ambiguity():
    src = read(RUN)
    assert "if ($PSBoundParameters.ContainsKey('Bind'))" in src
    assert "if ($PSBoundParameters.ContainsKey('Mode') -and $Mode -ne $legacyMode)" in src
    assert "throw '-Bind contradicts -Mode." in src
    assert "'0.0.0.0' { 'LAN' }" in src
    assert "'127.0.0.1' { 'LocalOnly' }" in src


def test_run_script_validates_port_and_uses_selected_listen_address():
    src = read(RUN)
    assert '[ValidateRange(1, 65535)]' in src
    assert '--host $listenAddress --port $Port --no-access-log' in src
    assert 'exit $LASTEXITCODE' in src


def test_dashboard_url_stays_local_even_in_lan_mode():
    src = read(RUN)
    assert 'Write-Host "Gateway UI: http://127.0.0.1:$Port"' in src
    assert 'Gateway network mode: $Mode' in src


def test_existing_token_security_is_preserved():
    src = read(RUN)
    for needle in ('.gateway-token', 'ConvertTo-SecureString', 'ConvertFrom-SecureString',
                   'ZeroFreeBSTR', 'AI_GATEWAY_TOKEN.Length -lt 24', 'secrets.token_urlsafe(32)'):
        assert needle in src
    assert not re.search(r'Write-(?:Host|Output).*\$env:AI_GATEWAY_TOKEN\b', src)


def test_no_network_or_firewall_mutation_is_automatic():
    src = read(START) + read(RUN) + read(FIREWALL)
    assert not re.search(r'(?mi)^\s*(?:New|Set|Remove)-NetFirewallRule\b', src)
    assert not re.search(r'(?mi)^\s*netsh\s+advfirewall\b', src)
    assert 'RemoteAddress' not in src
    assert '-Mode LAN -Port 8080' in src


def test_read_only_firewall_inspector_reports_profiles_and_listeners():
    src = read(FIREWALL)
    assert 'Get-NetConnectionProfile' in src
    assert 'Get-NetFirewallProfile' in src
    assert 'Get-NetTCPConnection' in src
    assert 'Get-NetFirewallRule' in src
    assert 'Windows Defender Firewall with Advanced Security' in src


def test_read_only_mode_stops_before_token_and_socket_creation():
    src = read(RUN)
    assert '[switch]$ShowNetworkConfig' in src
    assert src.index('if ($ShowNetworkConfig) {') < src.index("$tokenPath =")
    assert src.index('if ($ShowNetworkConfig) {') < src.index('& $venvPython -m uvicorn')


def test_windows_powershell_binding_matrix_without_starting_gateway():
    if os.name != 'nt':
        # Runtime integration executes on the user's Windows installation.
        assert '[switch]$ShowNetworkConfig' in read(RUN)
        return
    shell = shutil.which('powershell.exe') or shutil.which('powershell')
    assert shell, 'Windows PowerShell is required by Pinokio run.ps1'

    def inspect(*args):
        return subprocess.run(
            [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(RUN),
             '-ShowNetworkConfig', *args], capture_output=True, text=True, timeout=15,
        )

    checks = [
        (['-Mode', 'LAN'], 'listening on 0.0.0.0:8080'),
        (['-Mode', 'LocalOnly'], 'listening on 127.0.0.1:8080'),
        (['-Bind', '0.0.0.0'], 'listening on 0.0.0.0:8080'),
        (['-Bind', '127.0.0.1'], 'listening on 127.0.0.1:8080'),
    ]
    for args, expected in checks:
        run = inspect(*args)
        assert run.returncode == 0, f'{args}: {run.stdout} {run.stderr}'
        assert expected in run.stdout, f'{args}: {run.stdout}'
        assert 'Gateway UI: http://127.0.0.1:8080' in run.stdout

    for args in ([ '-Mode', 'LocalOnly', '-Bind', '0.0.0.0'],
                 ['-Bind', '192.168.1.65'], ['-Port', '0'], ['-Port', '65536']):
        run = inspect(*args)
        assert run.returncode != 0, f'{args} unexpectedly succeeded'


def test_instructions_make_remote_docker_and_firewall_limits_explicit():
    src = read(GUIDE)
    assert 'host.docker.internal' in src
    assert 'another physical computer' in src
    assert 'New-NetFirewallRule' in src
    assert '-Profile Private' in src
    assert '-RemoteAddress Any' in src
    assert 'no automatic firewall changes' in src.lower()
    assert 'port forwarding' in src.lower()
    assert 'HTTP' in src and 'TLS' in src
