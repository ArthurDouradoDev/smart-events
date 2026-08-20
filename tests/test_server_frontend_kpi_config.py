from pathlib import Path


HTML_PATH = Path(__file__).parents[1] / "server_frontend" / "index.html"


def test_event_form_configures_each_monitoring_object_type_explicitly():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="pm-task-list"' in html
    assert 'id="btn-add-pm-task"' in html
    assert "function addPmTaskRow" in html
    assert 'value="NRCELL"' in html
    assert 'value="NRDUCELL"' in html
    assert "integration: { pm_tasks: pmTasks }" in html
    assert "querySelectorAll('#pm-task-list .pm-task-row')" in html
    assert 'id="pm-task-4g"' not in html
    assert 'id="pm-task-nrcell"' not in html
    assert 'id="pm-task-nrducell"' not in html


def test_event_form_keeps_legacy_pm_task_as_4g_when_editing():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "if (!tasks.length && integration && Number(integration.pm_task_id) > 0)" in html
    assert "tasks.push({ tech: '4G', task_id: Number(integration.pm_task_id) })" in html
    assert "renderPmTaskRows(configuredPmTasks(event.integration))" in html
    assert "'4G': 'pm-task-4g'" not in html
    assert "'5G_NRCELL': 'pm-task-nrcell'" not in html
    assert "'5G_NRDUCELL': 'pm-task-nrducell'" not in html


def test_event_list_counts_tasks_per_technology():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "${taskLabels[tech]} ×${pmCounts[tech]}" in html
