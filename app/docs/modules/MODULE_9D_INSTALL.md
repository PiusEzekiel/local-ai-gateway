# Module 9D — LAN-aware Pinokio deployment and network guidance

## Scope and your confirmed preference

- **Default stays LAN-accessible:** Pinokio runs `run.ps1 -Mode LAN -Port 8080`; Uvicorn binds `0.0.0.0:8080` exactly as before.
- **No source-IP restrictions:** there is no host/IP allowlist in the app or launcher. The optional firewall rule below uses `-RemoteAddress Any` rather than an address or subnet restriction.
- **No automatic firewall changes:** ZIP installation and Pinokio startup do not add, remove, or modify Windows Firewall rules.
- **No token rotation, API changes, SQLite changes, ports changed, or n8n workflow changes.**
- A separate `-Mode LocalOnly` option exists for *future* Windows-only use. **Do not enable it while other computers run n8n.**

`0.0.0.0` means **all available IPv4 interfaces**, not “LAN only.” Actual reachability depends on Windows Firewall, router port forwarding, any VPN/tunnel and existing broader rules. A Private-profile firewall allow rule does not override other rules that already allow broad inbound traffic.

## Files in ZIP

- `start.js` — explicitly requests LAN mode; preserves `shell.run`, `local.set`, `process.wait` and URL-capture expression.
- `app/run.ps1` — validates LAN/LocalOnly bindings, retains legacy `-Bind 0.0.0.0` and `-Bind 127.0.0.1` manual commands, logs the selected binding without printing secrets, preserves gateway-token handling.
- `inspect-lan-firewall.ps1` — optional **read-only** network/firewall inspection. It does not modify settings.
- `app/tests/test_module_9d.py` — 12 launcher/security regression checks; on Windows one check executes the real PowerShell `-ShowNetworkConfig` argument matrix without starting Uvicorn or opening the token file.

`pinokio.js` was inspected but does not need replacement. We deliberately did not modify it.

## Before installation

1. Check that Module 9C.4 (test-isolation module) is installed and its Windows tests pass. Its expected total is **311** (9C.3 was 301); do not infer a passing result merely from the earlier ZIP download.
2. On your current Git hardening branch, save your verified working files: `git status --short`, `git add -A`, `git commit -m "Checkpoint before 9D LAN hardening"` (skip if clean), `git push`.
3. Allow active n8n jobs to finish; stop Local AI Gateway through Pinokio.
4. Back up your current `start.js`, `pinokio.js` and `app/run.ps1` to a secure folder outside the project. The ZIP replaces **only `start.js` and `app/run.ps1`** among the launcher files.
5. Extract the ZIP into `C:\pinokio\api\local-ai-gateway` preserving folders.

## Run tests

From `C:\pinokio\api\local-ai-gateway\app`:

```powershell
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Assuming the previous **311** tests passed, expected result is **323 passed** (12 added). We could not execute PowerShell or the full Windows runtime in the build environment; your Windows test is authoritative.

Optional read-only argument checks (do not require the gateway to be stopped and do not read the token):

```powershell
cd C:\pinokio\api\local-ai-gateway\app
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -ShowNetworkConfig -Mode LAN
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -ShowNetworkConfig -Mode LocalOnly
```

Expected:

- LAN → `listening on 0.0.0.0:8080` and local `Gateway UI: http://127.0.0.1:8080`.
- LocalOnly → `listening on 127.0.0.1:8080`.

## Start and check existing n8n

Start the gateway in Pinokio as normal. The log should now say:

```
Gateway network mode: LAN | listening on 0.0.0.0:8080
Gateway UI: http://127.0.0.1:8080
```

`Gateway UI` is the local dashboard link; it is **not** the URL for Docker on the other machine.

If Docker n8n runs **on another physical computer**, `host.docker.internal` refers to that *other computer's Docker host*, **not** the Windows Gateway computer. From n8n use a resolvable hostname or current LAN address of the **gateway computer**, for example `http://YOUR-GATEWAY-HOSTNAME:8080`. Do **not** change working n8n URL values for this module if they already reach the gateway. No IP restriction is introduced by this update.

Connectivity check from the n8n machine (does not require your secret):

```powershell
Test-NetConnection YOUR-GATEWAY-HOSTNAME -Port 8080
```

If its n8n Docker container is running, this optional check runs *inside* the container (replace the name and hostname):

```powershell
docker exec YOUR-N8N-CONTAINER node -e "fetch('http://YOUR-GATEWAY-HOSTNAME:8080/health').then(r => console.log('HTTP', r.status)).catch(e => { console.error(e.message); process.exit(1) })"
```

An HTTP `401` **without Authorization** confirms network reachability and auth enforcement. A `200` means that endpoint is public or differently protected; check the application's intended `/health` auth contract rather than concluding token auth has been bypassed. A connection failure needs DNS/firewall/routing investigation. Then run **one real authorized n8n text request and one reference-image request**; verify the Live badge/Jobs/Gallery.

## Optional Windows Firewall rule — manual, no IP allowlist

First inspect the gateway machine's **active** network category and existing rules:

```powershell
cd C:\pinokio\api\local-ai-gateway
powershell -NoProfile -ExecutionPolicy Bypass -File .\inspect-lan-firewall.ps1
```

On a network you trust, if the gateway machine's relevant Ethernet/Wi-Fi interface is classified **Private**, you may add a dedicated inbound rule. Run PowerShell **as Administrator** on the GATEWAY computer:

```powershell
New-NetFirewallRule `
  -DisplayName 'Local AI Gateway - Private LAN TCP 8080' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 `
  -Profile Private -RemoteAddress Any
```

Do not run that command if an identical dedicated rule already exists. `-RemoteAddress Any` means **no IP allowlist**; the only matching restrictions in this rule are Private network profile, inbound TCP and local port 8080. Anyone able to reach that interface and port can try the gateway. It is **not encryption**, **not a strict LAN boundary**, and does not remove broader inbound allow rules for Python or the same port. Review the actual effective policies in Windows Defender Firewall with Advanced Security (`wf.msc`). Never open or forward TCP 8080 on the router to the public internet. If an interface is Public, this *new* Private-only rule will not allow traffic on that interface; do not switch an untrusted network to Private just for the gateway.

Optional rollback of **the dedicated rule only**, if you previously created it (Administrator; confirm the name first):

```powershell
Get-NetFirewallRule -DisplayName 'Local AI Gateway - Private LAN TCP 8080'
Remove-NetFirewallRule -DisplayName 'Local AI Gateway - Private LAN TCP 8080' -Confirm
```

Do not remove unrelated Windows rules or globally disable Windows Firewall. Removing this rule may interrupt n8n if it was the only rule permitting access.

### HTTP bearer-token warning

The gateway uses HTTP by default. The bearer token is transmitted in an HTTP header, **not encrypted on the LAN**. Use only trusted networks; for untrusted networks, remote/mobile internet access or cross-site access, provide HTTPS through an appropriately configured reverse proxy or a secure VPN. The ZIP does not implement TLS.

## Optional LocalOnly mode (not for your current n8n setup)

If you someday move all clients to the same Windows computer, change only the command in `start.js` from `-Mode LAN` to `-Mode LocalOnly`. It binds `127.0.0.1:8080`, so other physical computers and their Docker containers will lose access. Restore `-Mode LAN` and restart to recover LAN access. Existing manual `-Bind 0.0.0.0` commands remain supported; conflicting `-Bind` and `-Mode` fail fast.

## Rollback

Stop Pinokio. Restore your backed-up `start.js` and `app/run.ps1`; restart through Pinokio. No database, token, environment, and n8n files need to be rolled back. Any firewall rule you chose to create must be managed separately; this package never created it.

## Verification criteria

- [ ] Full Windows pytest suite green (expected 323 if 9C.4 was green).
- [ ] Startup log says `LAN | listening on 0.0.0.0:8080`.
- [ ] Pinokio Dashboard link still opens at `127.0.0.1:8080`.
- [ ] An n8n request from the other machine succeeds using its existing gateway URL.
- [ ] n8n reference-image request succeeds and appears in Jobs and Gallery.
- [ ] Windows Firewall review shows intended Private-profile exposure and no unintended public/router forwarding.
- [ ] No secret token printed by startup and no firewall mutation without explicit admin action.
