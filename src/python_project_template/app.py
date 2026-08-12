import argparse
import logging
import logging.config
import pathlib

import yaml

from python_project_template.hello import Hello


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments.

    Returns:
        Parsed command line arguments.

    """
    parser = argparse.ArgumentParser(
        description="Run the engine with configuration files."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config/config.yaml",
        help="Path to the engine configuration file (YAML format).",
    )
    parser.add_argument(
        "-l",
        "--logging",
        type=str,
        default="config/logging.yaml",
        help="Path to the logging configuration file (YAML format).",
    )
    args, _ = parser.parse_known_args()
    return args


if __name__ == "__main__":
    args = _parse_args()
    with pathlib.Path(args.logging).open("r", encoding="utf-8") as f:
        logging_config = yaml.safe_load(f)
    logging.config.dictConfig(logging_config)

    logger = logging.getLogger("python_project_template")

    hello = Hello("World")
    logger.info(hello.greet())
