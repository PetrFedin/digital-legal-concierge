import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.migrations import current_database_revision, run_database_migrations


def main() -> None:
    run_database_migrations()
    current_database_revision()
    print("Миграции базы данных применены")


if __name__ == "__main__":
    main()
