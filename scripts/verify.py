"""Run required gates without hiding failed or unavailable commands."""

import subprocess
import sys

COMMANDS = [
    ["ruff", "check", "."],
    ["ruff", "format", "--check", "."],
    ["mypy", "appointment_booking"],
    ["alembic", "upgrade", "head"],
    ["pytest", "-q"],
]


def main() -> None:
    failures = []
    for command in COMMANDS:
        print("RUN", " ".join(command), flush=True)
        try:
            result = subprocess.run(command, check=False)
            if result.returncode:
                failures.append(" ".join(command))
        except FileNotFoundError:
            print("UNAVAILABLE", command[0], flush=True)
            failures.append(" ".join(command))
    if failures:
        print("Failed/unavailable gates:", "; ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
