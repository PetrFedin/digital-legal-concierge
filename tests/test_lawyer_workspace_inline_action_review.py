from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lawyer_workspace_uses_inline_review_instead_of_native_confirmation():
    source = read("app/api/lawyer_workspace.py")

    assert "confirm(" not in source
    assert "reviewCaseForm" in source
    assert "Проверить действие" in source
    assert "Подтвердить действие" in source
    assert "backToCaseEdit" in source
    assert "← Изменить данные" in source
    assert "data-stage=\"edit\"" in source
    assert "form.dataset.stage!=='review'" in source


def test_lawyer_workspace_preserves_comment_through_review_close_and_conflict():
    source = read("app/api/lawyer_workspace.py")

    assert "caseDrafts.set(draftKey(id,type),comment)" in source
    assert "caseDrafts.set(draftKey(id,form.dataset.type),textarea.value)" in source
    assert "Карточка дела изменилась. Черновик сохранён." in source
    assert "refreshCaseAfterConflict" in source
    assert "Карточка обновлена. Черновик сохранён" in source
    assert source.index("caseDrafts.delete(draftKey(id,type))") > source.index(
        "await api(paths[type]"
    )


def test_lawyer_workspace_keeps_http_conflict_status_for_recovery():
    source = read("app/api/lawyer_workspace.py")

    assert "e.status=r.status" in source
    assert "if(e.status===409)" in source
    assert "Обновить карточку" in source


def test_lawyer_workspace_never_exposes_raw_machine_status_in_action_success():
    source = read("app/api/lawyer_workspace.py")

    assert "+result.status" not in source
    assert "Дело принято. Этап договора открыт." in source
    assert "Запрос документов отправлен клиенту." in source
    assert "Дело переведено в консультационный маршрут." in source


def test_lawyer_workspace_keeps_exact_case_links_and_write_refresh_separation():
    source = read("app/api/lawyer_workspace.py")

    assert 'href=\"/message-center/ui?case_id=${x.case_id}\"' in source
    assert 'href=\"/document-access/review/ui?case_id=${x.case_id}\"' in source
    assert "Операция сохранена, но кабинет не обновился" in source
    assert "Дело принято, но кабинет не обновился" in source
    assert "Запрос сохранён, но кабинет не обновился" in source
    submit = source[source.index("async function submitCaseForm") :]
    assert submit.index("await api(paths[type]") < submit.index(
        "caseDrafts.delete(draftKey(id,type))"
    ) < submit.index("await load(null,true)")
