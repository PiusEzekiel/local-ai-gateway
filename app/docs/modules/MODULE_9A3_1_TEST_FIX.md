# Module 9A.3.1 — Windows HTTPS test compatibility

This is a **test-only** correction for `test_pinned_tls_sni_uses_original_hostname_not_ip` failing during construction of `http.client.HTTPSConnection` with `AttributeError: 'FakeContext' object has no attribute 'verify_mode'`.

The mocked TLS context now exposes `verify_mode = ssl.CERT_REQUIRED` and `check_hostname = True`, which matches the verification-related attributes inspected by the Windows/Python standard-library HTTPS connection. The original mock behavior continues to check that the connection uses a pinned IP and passes `drive.google.com` as the TLS SNI hostname.

No gateway source, request handling, security policy, settings, or data is changed by this patch.

## Apply

1. Back up `C:\pinokio\api\local-ai-gateway\app\tests\test_module_9a3.py` if desired.
2. Extract the accompanying ZIP at `C:\pinokio\api\local-ai-gateway`, preserving paths and replacing **only** `app\tests\test_module_9a3.py`.
3. No Pinokio restart is needed: this updates only a regression test.
4. Rerun the existing full-suite command:

```powershell
cd C:\pinokio\api\local-ai-gateway\app
$TestBase = Join-Path $env:LOCALAPPDATA 'LocalAIGatewayPytestScratch'
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp "$TestBase"
```

Expected result on your installation: **225 passed**. Verify a real n8n image request with a Google Drive reference afterward to complete the runtime smoke check.

## Manual alternative

Add `import ssl` to the top of `test_module_9a3.py`, then add the following inside the test's `FakeContext` class, before `wrap_socket`:

```python
verify_mode = ssl.CERT_REQUIRED
check_hostname = True
```
