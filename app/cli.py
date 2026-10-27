"""Operational commands: `python -m app.cli init-storage`."""

import argparse
import logging

from app.config import get_settings
from app.logging_config import configure_logging
from app.storage import get_storage


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-storage", help="Create buckets and apply access policies")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if args.command == "init-storage":
        get_storage().ensure_buckets()
        logging.getLogger(__name__).info("Storage initialised")


if __name__ == "__main__":
    main()
