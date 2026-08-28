from __future__ import annotations

from pathlib import Path

from app.api.workdesk_renderer import render_workdesk_html


def test_workdesk_renderer_composes_each_cross_cutting_layer_once() -> None:
    html = render_workdesk_html()

    assert html.count('id="processIntegrityBanner"') == 1
    assert html.count("/admin/workdesk/cases/'+id+'/responsibility") == 1
    assert html.count("/admin/case-assignment/cases/'+id+'/repair-unreachable") == 1
    assert "new URLSearchParams(window.location.search).get('case_id')" in html
    assert html.count("</body>") == 1


def test_assignment_queue_uses_renderer_instead_of_patching_html() -> None:
    source = Path("app/api/assignment_queue.py").read_text(encoding="utf-8")

    assert "from app.api.workdesk_renderer import render_workdesk_html" in source
    assert "return HTMLResponse(render_workdesk_html())" in source
    assert "WORKDESK_HTML" not in source
    assert "inject_workdesk_integrity" not in source
    assert "_WORKDESK_RESPONSIBILITY_PATCH" not in source
    assert "_inject_workdesk_patch" not in source
