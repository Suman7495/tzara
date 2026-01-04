"""
Filesystem helper utilities for Tzara.

All functions here are:
- side-effect explicit
- reusable
- domain-agnostic
"""

from pathlib import Path
import shutil
from typing import List


def list_pdfs(directory: Path) -> List[Path]:
    """
    Return a list of PDF files in a directory.

    Args:
        directory: Directory to scan.

    Returns:
        List of Path objects pointing to PDF files.
    """
    if not directory.exists():
        return []

    return [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    ]


def check_dir(directory: Path) -> None:
    """
    Ensure that a directory exists.

    Args:
        directory: Directory path to create if missing.
    """
    directory.mkdir(parents=True, exist_ok=True)


def move_file(source: Path, destination: Path, *, dry_run: bool = True) -> None:
    """
    Move a file from source to destination.

    Args:
        source: Path to source file.
        destination: Destination path.
        dry_run: If True, do not perform the move.

    Raises:
        FileNotFoundError: If source does not exist.
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    if dry_run:
        return

    shutil.move(str(source), str(destination))
