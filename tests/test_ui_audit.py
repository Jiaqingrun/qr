"""Web UI 静态自检。"""
from qr import ui_audit


def test_audit_ui_passes():
    rep = ui_audit.audit_ui(strict_api=True)
    assert rep["buttons"] >= 80
    errors = [i for i in rep["issues"] if i["level"] == "error"]
    assert not errors, errors


def test_no_false_duplicate_ids_from_js_templates():
    html = '<div id="a"></div><script>const x=`id="${id}"`;</script>'
    assert ui_audit._duplicate_ids(html) == []


def test_format_issues():
    lines = ui_audit.format_issues(
        [{"level": "error", "area": "ui_button", "message": "test"}],
        limit=5,
    )
    assert lines and "test" in lines[0]
