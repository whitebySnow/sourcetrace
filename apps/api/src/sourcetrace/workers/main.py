from sourcetrace.core.logging import configure_logging, get_logger


def main() -> None:
    configure_logging("INFO")
    get_logger(__name__).info("worker_placeholder_started")
    raise SystemExit("Worker queue adapter will be added with document ingestion.")


if __name__ == "__main__":
    main()
