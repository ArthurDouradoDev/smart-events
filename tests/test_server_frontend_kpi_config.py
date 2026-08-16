from pathlib import Path


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"


def test_event_form_configures_each_monitoring_object_type_explicitly():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="pm-task-4g"' in html
    assert 'id="pm-task-nrcell"' in html
    assert 'id="pm-task-nrducell"' in html
    assert "integration: { pm_tasks: pmTasks }" in html
    assert "['NRCELL', document.getElementById('pm-task-nrcell').value]" in html
    assert "['NRDUCELL', document.getElementById('pm-task-nrducell').value]" in html


def test_event_form_keeps_legacy_pm_task_as_4g_when_editing():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "if (!tasks.length && integration && Number(integration.pm_task_id) > 0)" in html
    assert "tasks.push({ tech: '4G', task_id: Number(integration.pm_task_id) })" in html
    assert "'5G_NRCELL': 'pm-task-nrcell'" in html
    assert "'5G_NRDUCELL': 'pm-task-nrducell'" in html
