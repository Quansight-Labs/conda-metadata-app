"""
This module provides a way to retrieve version information about conda-metadata-app.
"""

import datetime
import subprocess

from pydantic import BaseModel, Field

VERSION_INFO_FILE = "version_info.json"


class VersionInfo(BaseModel):
    git_hash: str = Field(pattern=r"^[0-9a-f]{40}$")
    timestamp: datetime.datetime

    @property
    def short_git_hash(self):
        return self.git_hash[:7]

    def timestamp_string(self):
        return self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    def __str__(self):
        return f"{self.short_git_hash} ({self.timestamp_string()})"


def get_version_info_from_git() -> VersionInfo:
    """
    Returns version information from the `.git` directory.
    This requires you to have the `git` command line tool installed.

    You should only use this function from a Streamlit Cloud deployment or
    during the Docker build process, as neither git nor the `.git` directory
    will be available in the final Docker image!
    """
    git_info = subprocess.check_output(
        [
            "git",
            "--no-pager",
            "log",
            "-1",
            "--pretty=format:%H-%cd",
            "--date=unix",
        ],
        text=True,
    ).strip()

    git_hash, timestamp = git_info.split("-")

    return VersionInfo(
        git_hash=git_hash,
        timestamp=datetime.datetime.fromtimestamp(int(timestamp), datetime.UTC),
    )


def save_version_info_from_git() -> None:
    """
    Saves the version information from git to version_info.json.
    See the `get_version_info_from_git` function for more information.
    """
    version_info = get_version_info_from_git()

    with open(VERSION_INFO_FILE, "w") as f:
        f.write(version_info.model_dump_json(indent=2))


def get_version_info() -> VersionInfo:
    """
    Get the version information of the current app installation.
    This works as follows:
    1. Retrieve the version information from the `version_info.json` file (relevant for Docker image).
    2. If the file does not exist, retrieve the version information from git (relevant for Streamlit Cloud).
    """
    try:
        with open(VERSION_INFO_FILE) as f:
            return VersionInfo.model_validate_json(f.read())
    except FileNotFoundError:
        return get_version_info_from_git()


if __name__ == "__main__":
    save_version_info_from_git()
