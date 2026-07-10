install:
	pip install -e .

init-db:
	python scripts/init_db.py

run:
	python -m app.main

smoke:
	python scripts/e2e_smoke.py

scheduler:
	python scripts/run_scheduler.py

check:
	python -m compileall app scripts
	python scripts/e2e_smoke.py
