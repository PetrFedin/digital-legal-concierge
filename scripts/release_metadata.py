from __future__ import annotations

import argparse
import json

from app.release import release_metadata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--field",
        choices=(
            "application_version",
            "release",
            "git_commit",
            "build_timestamp",
            "image_repository",
            "image_tag",
            "migration_heads",
        ),
    )
    arguments = parser.parse_args()
    metadata = release_metadata()
    if arguments.field:
        value = metadata[arguments.field]
        if isinstance(value, list):
            print(",".join(str(item) for item in value))
        else:
            print(value)
    else:
        print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
