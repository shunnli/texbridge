"""Read-only project directory discovery for inverse search."""

import gzip
from pathlib import Path
import zlib


MAX_HEADER_BYTES = 1024 * 1024
MAX_PDFS = 128


def find_git_root(directory: Path) -> Path | None:
    """Find the closest enclosing repository, including worktrees/submodules."""
    directory = directory.resolve()
    for candidate in (directory, *directory.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            return candidate
    return None


def _contains_source(pdf: Path, source: Path) -> bool:
    """Read only SyncTeX input records, never its page/position data."""
    for suffix in (".synctex", ".synctex.gz"):
        sync = pdf.with_suffix(suffix)
        opener = gzip.open if suffix.endswith(".gz") else open
        try:
            with opener(sync, "rb") as stream:
                remaining = MAX_HEADER_BYTES
                inputs: list[Path] = []
                while remaining > 0:
                    line = stream.readline(remaining + 1)
                    if not line or len(line) > remaining:
                        break
                    remaining -= len(line)
                    if line.startswith(b"Content:"):
                        # Relative inputs normally refer to the build working
                        # directory. An absolute first input can identify it
                        # when the PDF is written to a separate output folder.
                        bases = [pdf.parent]
                        if inputs and inputs[0].is_absolute():
                            bases.append(inputs[0].parent)
                        for item in inputs:
                            paths = [item] if item.is_absolute() else [base / item for base in bases]
                            if any(path.resolve() == source for path in paths):
                                return True
                        break
                    if line.startswith(b"Input:"):
                        parts = line.decode("utf-8").rstrip("\r\n").split(":", 2)
                        if len(parts) == 3 and parts[1].isdigit() and parts[2]:
                            inputs.append(Path(parts[2]))
        except (OSError, EOFError, UnicodeError, ValueError, zlib.error):
            # Missing, stale, unreadable or malformed metadata is not a reason
            # to prevent inverse navigation to a valid source file.
            continue
    return False


def find_project(source: Path, pdf: Path | None = None) -> Path | None:
    """Prefer a Git root, then a PDF folder proved by SyncTeX input records."""
    source = source.resolve()
    if pdf is not None:
        directory = pdf.resolve().parent
        return find_git_root(directory) or directory
    repository = find_git_root(source.parent)
    if repository is not None:
        return repository

    # Bound work on every invocation. Avoid broad recursive scans and do not
    # inspect .aux directories. A missed match falls back to a single file.
    directories: set[Path] = set()
    for ancestor in list(source.parents)[:5]:
        if ancestor == Path(ancestor.anchor):
            break
        directories.add(ancestor)
        directories.update(ancestor / name for name in ("build", "out", "output"))
    matches: set[Path] = set()
    seen = 0
    for directory in sorted(directories):
        try:
            for candidate in directory.glob("*.pdf"):
                if not candidate.is_file():
                    continue
                seen += 1
                if seen > MAX_PDFS:
                    return None
                if _contains_source(candidate, source):
                    matches.add(candidate.parent.resolve())
                    if len(matches) > 1:
                        return None
        except OSError:
            continue
    if not matches:
        return None
    directory = matches.pop()
    return find_git_root(directory) or directory
