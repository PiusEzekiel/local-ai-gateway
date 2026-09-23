"""Guardrails for the frontend-only Settings UX refresh."""
from pathlib import Path

GATEWAY = Path(__file__).resolve().parents[1] / 'gateway'


def test_duration_controls_preserve_server_native_units():
    js = (GATEWAY / 'dashboard' / 'settings.js').read_text(encoding='utf-8')
    assert 'durationStoredValue(el.value, $(`setting-${key}-unit`).value, native)' in js
    # Cache freshness has pre-defined choices; it must remain a select and must
    # never try to read a non-existent separate units picker.
    assert 'native && !field.choices?.length' in js
    assert 'field.choices?.length' in js
    assert 'Number.isSafeInteger(value)' in js
    assert 'const patch = currentPatch();' in js


def test_sections_and_maintenance_anchors_are_not_removed():
    js = (GATEWAY / 'dashboard' / 'settings.js').read_text(encoding='utf-8')
    html = (GATEWAY / 'dashboard.html').read_text(encoding='utf-8')
    for anchor in ('settings-reference-cache', 'settings-storage',
                   'settings-privacy-storage', 'settings-system', 'referenceCacheSettings'):
        assert f'id="{anchor}"' in html
        assert anchor in js or anchor == 'referenceCacheSettings'
    assert 'showSettingsSection(view.activeSection);' in js
    assert 'target.scrollIntoView({behavior: "auto", block: "start"});' in js


def test_changes_are_frontend_only():
    settings_js = (GATEWAY / 'dashboard' / 'settings.js').read_text(encoding='utf-8')
    assert 'getSettings, patchSettings, getPrivacyStorage, purgePrivacy' in settings_js
    assert 'const list = isCache ? cacheFields : node("div", "settings-fields");' in settings_js
    assert 'innerHTML' not in settings_js
