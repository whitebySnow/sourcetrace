from dramatiq.cli import main as dramatiq_main

from sourcetrace.core.logging import configure_logging


def main() -> None:
    configure_logging("INFO")
    dramatiq_main(["sourcetrace.workers.tasks"])  # type: ignore[no-untyped-call]


if __name__ == "__main__":
    main()
