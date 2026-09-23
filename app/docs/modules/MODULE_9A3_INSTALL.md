# Module 9A.3 — Secure Reference-Image Downloads

**Baseline:** Windows-verified Module 9A.2 on your existing private hardening branch. This is an incremental patch, **not** a full gateway replacement.

## Contents

- `app/gateway/reference_images.py` — one-hop-at-a-time validated Google-hosted HTTPS reference downloader; disabled automatic redirects and environment proxies; public IP verification and pinned TLS connections.
- `app/tests/test_module_9a3.py` — 40 no-network test cases, including redirect SSRF, private and reserved DNS, mixed DNS, Google Drive redirect compatibility, logging redaction, size limits, and TLS SNI.
- This guide.

**No DB migration, `.gateway-data`, `.gateway-token`, `.gateway-settings.json`, `quota_monitor.py`, source image, or frontend changes are included.** Normal successful image generation and response contracts remain unchanged.

## Behavioral changes

1. Only HTTPS Google reference hosts already allowed by the original downloader may be contacted: `drive.google.com`, `drive.usercontent.google.com`, and subdomains of `.googleusercontent.com`.
2. Initial and redirected URLs must not have embedded credentials, unexpected ports, malformed hostnames, control characters or an HTTP downgrade.
3. Every redirect is inspected **before** a network request. At most **five redirects** are followed; relative HTTPS redirects within the allowed hosts work.
4. The full set of IPv4/IPv6 DNS results for *each hop* must be globally routable. If **any** result is private, loopback, link-local, reserved, or otherwise not global, the request is rejected.
5. The actual socket is connected to one of the checked numeric IPs; TLS SNI and certificate/hostname verification use the approved domain. DNS cannot silently change the destination between policy check and connection.
6. Environment HTTP(S) proxies are deliberately bypassed because an intermediary that re-resolves the hostname would defeat IP pinning. **If your internet access requires a corporate proxy, this update may prevent reference downloads; do the Windows real-reference smoke check.** The gateway still sends no reference URL/query material to its own logs.
7. Retained behavior: max 20 MiB, PNG/JPEG/WebP signature checks, reference-ID-compatible filenames, existing `GatewayError` kind/status conventions (`invalid_reference_image`/422, `reference_download_failed`/502).

**Scope:** This specifically closes Copilot's redirect/SSRF finding. It does not change Gallery cards, image lightbox, Jobs reference thumbnails or Gallery reference grouping; those are the approved **9B.1 and 9B.2** frontend modules.

## Install and test (Windows/Pinokio)

1. In PowerShell at `C:\pinokio\api\local-ai-gateway`, check `git status --short` and commit your verified 9A.2 modifications if pending. Leave `v0.9-control-plane-complete` intact.
2. Let any n8n image jobs complete and stop the gateway in Pinokio.
3. Back up `app/gateway` and `app/.gateway-data`. Extract the ZIP **into the repository root**, preserving `app/` and replacing only the supplied files.
4. Check `git diff --stat`: the only existing source file changed should be `app/gateway/reference_images.py`, plus one new test and this guide outside the app.
5. Run:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
New-Item -ItemType Directory -Force -Path $TestBase | Out-Null
.\.venv\Scripts\python.exe -m compileall -q gateway tests
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

**Expected full Windows result: 225 passed** if the preceding verified 9A.2 suite had 185 tests and is unchanged (+40 new parametrized tests). The local available regression subset passed **204 tests** (previous local 164 + 40). The available harness does not include your exact Windows original phase-one code; your Windows suite is authoritative.

6. Restart Pinokio. Check Healthy/Live, then make a **real n8n image request using the usual Google Drive reference link(s)**. Verify the generated image opens in Gallery and its references display in Jobs. Also make one ordinary text request if convenient.
7. An intentionally disallowed/private redirect should fail with 422, and a failed network/DNS connection returns 502 without leaking URL query strings to gateway logs. You do not need to make an unsafe live-network test: these paths have no-network tests.

## Rollback / commit

If tests fail, keep Pinokio stopped and restore `reference_images.py` from the prior verified commit or your backup, and remove the newly added test file. **Do not reset `.gateway-data`** as part of ordinary code rollback. Once tested and smoke-checked, commit the change on your hardening branch:

```powershell
cd C:\pinokio\api\local-ai-gateway
git add app/gateway/reference_images.py app/tests/test_module_9a3.py
git commit -m "Hardening 9A.3 - validate pinned HTTPS reference redirects"
git push -u origin HEAD
```

If `git push -u origin HEAD` is rejected due to branch protections or connectivity, use your normal branch-specific push after checking `git branch --show-current`.
