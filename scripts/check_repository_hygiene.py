"""Check tracked text files for broken local links and likely committed credentials."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import unquote

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\((?P<target>[^)\n]+)\)")
SECRET_PATTERNS = (
    ("long sk credential", re.compile(r"sk-[A-Za-z0-9]{24,}")),
    ("workspace credential", re.compile(r"sk-ws-H\.[A-Za-z0-9._-]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
PUBLICATION_BLOCKED_PATH_PARTS = frozenset(
    {
        "drafts",
        "experiments",
        "internal",
        "paper",
        "patent",
        "planning",
        "private-notes",
        "research",
    }
)
PUBLICATION_BLOCKED_PATH_PREFIXES = (
    "docs/datasets/",
    "benchmarks/manifests/",
    "benchmark-data/",
    "benchmark-results/",
)
PUBLICATION_ALLOWED_JSONL = frozenset({"benchmarks/ctxttlbench.jsonl"})


def tracked_files(root: Path) -> list[Path]:
    """Return repository-relative paths tracked by Git."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _link_destination(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    return unquote(target)


def check_markdown_links(root: Path, paths: Iterable[Path]) -> list[str]:
    """Return errors for local Markdown destinations that do not exist."""
    errors: list[str] = []
    for relative_path in paths:
        if relative_path.suffix.lower() != ".md":
            continue
        text = _read_text(root / relative_path)
        if text is None:
            continue
        for match in MARKDOWN_LINK.finditer(text):
            destination = _link_destination(match.group("target"))
            lowered = destination.lower()
            if not destination or destination.startswith("#"):
                continue
            if lowered.startswith(("http://", "https://", "mailto:", "codex://")):
                continue
            file_part = destination.split("#", maxsplit=1)[0]
            if file_part.startswith("/"):
                candidate = root / file_part.lstrip("/")
            else:
                candidate = root / relative_path.parent / file_part
            if not candidate.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{relative_path.as_posix()}:{line}: missing link target")
    return errors


def check_secret_shapes(root: Path, paths: Iterable[Path]) -> list[str]:
    """Return locations of high-confidence credential shapes without printing their values."""
    errors: list[str] = []
    for relative_path in paths:
        text = _read_text(root / relative_path)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    errors.append(f"{relative_path.as_posix()}:{line_number}: possible {label}")
    return errors


def check_publication_boundary(paths: Iterable[Path]) -> list[str]:
    """Require review before publishing non-product directories or datasets."""
    errors: list[str] = []
    for relative_path in paths:
        name = relative_path.as_posix()
        lowered = name.lower()
        parts = {part.lower() for part in relative_path.parts}
        if parts & PUBLICATION_BLOCKED_PATH_PARTS or lowered.startswith(
            PUBLICATION_BLOCKED_PATH_PREFIXES
        ):
            errors.append(f"{name}: path is outside the public product boundary")
            continue
        if relative_path.suffix.lower() == ".jsonl" and lowered not in PUBLICATION_ALLOWED_JSONL:
            errors.append(f"{name}: dataset requires an explicit publication review")
            continue
    return errors


def check_doc_locales(root: Path, manifest_path: Path) -> list[str]:
    """Validate the documentation locale-pack manifest without interpreting page content."""
    try:
        manifest = tomllib.loads((root / manifest_path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{manifest_path.as_posix()}: invalid locale manifest: {exc}"]

    locales = manifest.get("locales")
    default_locale = manifest.get("default_locale")
    pages = manifest.get("pages")
    if not isinstance(locales, list) or not locales or not all(isinstance(x, str) for x in locales):
        return [f"{manifest_path.as_posix()}: locales must be a non-empty string list"]
    if len(locales) != len(set(locales)):
        return [f"{manifest_path.as_posix()}: locale identifiers must be unique"]
    if default_locale not in locales:
        return [f"{manifest_path.as_posix()}: default_locale must be declared in locales"]
    if not isinstance(pages, list) or not pages:
        return [f"{manifest_path.as_posix()}: pages must be a non-empty table list"]

    errors: list[str] = []
    page_ids: set[str] = set()
    known_locales = set(locales)
    for index, page in enumerate(pages, start=1):
        page_id = page.get("id") if isinstance(page, dict) else None
        paths = page.get("paths") if isinstance(page, dict) else None
        if not isinstance(page_id, str) or not page_id:
            errors.append(f"{manifest_path.as_posix()}: page {index} has no valid id")
            continue
        if page_id in page_ids:
            errors.append(f"{manifest_path.as_posix()}: duplicate page id {page_id}")
        page_ids.add(page_id)
        if not isinstance(paths, dict):
            errors.append(f"{manifest_path.as_posix()}: page {page_id} has no paths table")
            continue
        missing = known_locales - set(paths)
        extra = set(paths) - known_locales
        if missing:
            errors.append(
                f"{manifest_path.as_posix()}: page {page_id} misses locales {sorted(missing)}"
            )
        if extra:
            errors.append(
                f"{manifest_path.as_posix()}: page {page_id} has undeclared locales {sorted(extra)}"
            )
        for locale, relative in paths.items():
            if not isinstance(relative, str) or not relative:
                errors.append(
                    f"{manifest_path.as_posix()}: page {page_id} locale {locale} has no path"
                )
            elif not (root / relative).is_file():
                errors.append(
                    f"{manifest_path.as_posix()}: page {page_id} locale {locale} path is missing"
                )
    return errors


def run_checks(root: Path, paths: Sequence[Path] | None = None) -> list[str]:
    selected_paths = list(paths) if paths is not None else tracked_files(root)
    return [
        *check_markdown_links(root, selected_paths),
        *check_secret_shapes(root, selected_paths),
        *check_publication_boundary(selected_paths),
        *check_doc_locales(root, Path("docs/locales.toml")),
    ]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = run_checks(root)
    if errors:
        print("Repository hygiene checks failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository hygiene checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
