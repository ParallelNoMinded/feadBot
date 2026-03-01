import logging
import subprocess
from typing import List, Optional, Union

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="[%(asctime)s] - %(levelname)s - %(process)d: %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
logger.setLevel("DEBUG")


def _run_alembic_command(cmd: Union[List[str], str]) -> List[str]:
    if isinstance(cmd, str):
        cmd = [cmd]

    cmd = ["alembic"] + cmd
    cmd_str = " ".join(cmd)

    logger.info("Running command: %s", cmd_str)

    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        stdout, stderr = process.communicate()
        output = stderr.split("\n") + stdout.split("\n")

        clear_output = [
            output_line
            for output_line in output
            if output_line and not (output_line.strip(" ").endswith("*") and output_line.strip(" ").startswith("*"))
        ]
        clear_output_str = "\n".join(clear_output)

        logger.info("Command result:\n%s", clear_output_str)

        return clear_output


def alembic_upgrade() -> None:
    _run_alembic_command(cmd=["upgrade", "head"])


def alembic_current() -> Optional[str]:
    output = _run_alembic_command(cmd="current")
    version_tag = "(head)"

    if not output:
        return None

    output_last_line = output[-1]

    return output_last_line if output_last_line.endswith(version_tag) else None


def alembic_head() -> Optional[str]:
    output = _run_alembic_command(cmd="heads")

    if not output:
        return None

    output_last_line = output[-1]
    return output_last_line


if __name__ == "__main__":
    db_current_version = alembic_current()
    db_head_version = alembic_head()

    logger.info(
        "DB current version: %s\nDB latest version: %s",
        db_current_version,
        db_head_version,
    )

    if db_current_version != db_head_version:
        alembic_upgrade()
    else:
        logger.info("Migration skipped, DB currently in latest version")
