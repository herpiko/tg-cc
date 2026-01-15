"""Entry point for tg-cc bot."""

import argparse
import os

from .bot import run


def main():
    """Main entry point for the tg-cc command."""
    parser = argparse.ArgumentParser(
        description="Telegram bot that integrates Claude AI for software development tasks."
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to config.yaml file (default: ./config.yaml)"
    )

    args = parser.parse_args()

    # If config path not specified, look in current directory
    config_path = args.config
    if config_path is None:
        config_path = os.path.join(os.getcwd(), "config.yaml")
        if not os.path.exists(config_path):
            # Try looking relative to package
            config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
            config_path = os.path.abspath(config_path)

    run(config_path)


if __name__ == "__main__":
    main()
