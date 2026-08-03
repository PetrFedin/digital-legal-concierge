from __future__ import annotations

from pathlib import Path


def test_docker_runs_supervised_process_entrypoint():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["dlc-entrypoint"]' in dockerfile
    assert 'CMD ["python", "-m", "app.process"]' in dockerfile
    assert 'exec "$@"' in entrypoint
    assert "python -m app.main" not in dockerfile


def test_process_uses_supervisor_for_all_background_services():
    source = Path("app/process.py").read_text(encoding="utf-8")

    assert "supervise_server(server, build_background_services())" in source
    assert 'BackgroundService(name="telegram-bot"' in source
    assert 'BackgroundService(name="scheduler"' in source
    assert "asyncio.create_task" not in source


def test_bare_background_tasks_remain_outside_production_entrypoint():
    source = Path("app/process.py").read_text(encoding="utf-8")
    supervisor = Path("app/service_supervisor.py").read_text(encoding="utf-8")

    assert "asyncio.create_task" not in source
    assert 'name="http-server"' in supervisor
    assert 'name=f"service:{service.name}"' in supervisor
