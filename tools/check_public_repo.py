"""Reject data artifacts from the Git index and reachable history.

The scanner deliberately asks Git for tracked paths. It never walks the
working tree, so ignored local study data may remain in place safely.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


PROTECTED_ROOTS = {
    ".agents",
    ".codebase-memory",
    ".codex",
    ".venv",
    "backups",
    "data",
    "tmp",
    "题库",
}

PROTECTED_SUFFIXES = {
    ".7z",
    ".bak",
    ".bmp",
    ".csv",
    ".db",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".rar",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".svg",
    ".tif",
    ".tiff",
    ".tsv",
    ".vce",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}

SPECIAL_SUFFIXES = (".db-wal", ".db-shm")


@dataclass(frozen=True)
class Finding:
    source: str
    path: str
    reason: str
    commit: str | None = None


class GitCheckError(RuntimeError):
    pass


def _git(repo: Path, *args: str, text: bool = False) -> str | bytes:
    command = ["git", "-c", "core.quotePath=false", "-C", str(repo), *args]
    result = subprocess.run(command, capture_output=True, text=text, check=False)
    if result.returncode:
        error = result.stderr.strip() if text else result.stderr.decode("utf-8", "replace").strip()
        raise GitCheckError(error or f"Git command failed: {' '.join(command)}")
    return result.stdout


def sensitive_reason(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if parts and parts[0].casefold() in {item.casefold() for item in PROTECTED_ROOTS}:
        return f"protected directory: {parts[0]}/"

    filename = pure.name.casefold()
    if filename == ".env" or filename.startswith(".env."):
        return "environment file"
    if filename.endswith(SPECIAL_SUFFIXES):
        return "SQLite runtime file"
    if pure.suffix.casefold() in PROTECTED_SUFFIXES:
        return f"protected file type: {pure.suffix.casefold()}"
    return None


def tracked_paths(repo: Path) -> list[str]:
    output = _git(repo, "ls-files", "-z", "--cached")
    return [item.decode("utf-8", "surrogateescape") for item in output.split(b"\0") if item]


def history_paths(repo: Path) -> list[str]:
    output = _git(repo, "rev-list", "--objects", "--all", text=True)
    paths = []
    for line in output.splitlines():
        _object_id, separator, path = line.partition(" ")
        if separator and path:
            paths.append(path)
    return paths


def first_history_commit(repo: Path, path: str) -> str | None:
    output = _git(repo, "log", "--all", "--diff-filter=A", "--format=%H", "--", path, text=True)
    commits = [line.strip() for line in output.splitlines() if line.strip()]
    return commits[-1] if commits else None


def scan_repository(repo: Path, include_history: bool = True) -> list[Finding]:
    repo = repo.resolve()
    _git(repo, "rev-parse", "--is-inside-work-tree", text=True)
    findings: list[Finding] = []

    for path in tracked_paths(repo):
        reason = sensitive_reason(path)
        if reason:
            findings.append(Finding("index", path, reason))

    if include_history:
        indexed = {(item.path, item.reason) for item in findings}
        seen: set[str] = set()
        for path in history_paths(repo):
            reason = sensitive_reason(path)
            if not reason or path in seen:
                continue
            seen.add(path)
            if (path, reason) in indexed:
                continue
            findings.append(Finding("history", path, reason, first_history_commit(repo, path)))

    return sorted(findings, key=lambda item: (item.source, item.path.casefold()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check that a Git repository contains no local study data")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    parser.add_argument("--index-only", action="store_true", help="skip reachable-history inspection")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        findings = scan_repository(args.repo, include_history=not args.index_only)
    except GitCheckError as exc:
        print(f"PUBLIC REPOSITORY CHECK ERROR: {exc}", file=sys.stderr)
        return 2

    if findings:
        print("PUBLIC REPOSITORY CHECK FAILED")
        for finding in findings:
            location = f" commit={finding.commit}" if finding.commit else ""
            print(f"- [{finding.source}] {finding.path}: {finding.reason}{location}")
        print("Remove the file from the Git index/history; do not delete ignored local data.")
        return 1

    print("PUBLIC REPOSITORY CHECK PASSED: no protected data artifacts are tracked or present in history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
