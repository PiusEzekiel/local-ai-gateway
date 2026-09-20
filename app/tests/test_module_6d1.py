"""6D.1 regression guard for browser cache mismatch seen on Windows."""
from pathlib import Path
import re

G = Path(__file__).resolve().parents[1] / "gateway"
VERSION = "9c1-20260920"

def test_distinct_diagnostics_css_and_versioned_html():
    html = (G / "dashboard.html").read_text(encoding="utf-8")
    assert f"/dashboard/assets/dashboard.css?v={VERSION}" in html
    assert f"/dashboard/assets/diagnostics.css?v={VERSION}" in html
    assert f"/dashboard/assets/dashboard.js?v={VERSION}" in html
    assert ".diag-summary" in (G / "dashboard" / "diagnostics.css").read_text(encoding="utf-8")
    assert ".diag-summary" not in (G / "dashboard" / "dashboard.css").read_text(encoding="utf-8")

def test_all_frontend_module_imports_use_one_release():
    ui = G / "dashboard"
    versions = set()
    for path in ui.glob("*.js"):
        for target in re.findall(r'from "(\./[^"]+\.js[^"]*)"', path.read_text(encoding="utf-8")):
            assert "?v=" in target, f"Unversioned import in {path.name}: {target}"
            versions.add(target.split("?v=", 1)[1])
    assert versions == {VERSION}
    main = (ui / "dashboard.js").read_text(encoding="utf-8")
    diagnostics = (ui / "diagnostics.js").read_text(encoding="utf-8")
    assert f'from "./api.js?v={VERSION}"' in main
    assert f'}} from "./api.js?v={VERSION}"' in diagnostics

def test_initial_diagnostics_state_and_form_autofill():
    html = (G / "dashboard.html").read_text(encoding="utf-8")
    for id in ("diagSearch", "diagTask", "diagOperation", "diagModel", "diagErrorType"):
        assert re.search(fr'id="{id}" autocomplete="off"', html)
