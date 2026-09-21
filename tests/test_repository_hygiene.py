from pathlib import Path

from scripts.check_repository_hygiene import (
    check_doc_locales,
    check_markdown_links,
    check_publication_boundary,
    check_secret_shapes,
)


def test_markdown_link_check_accepts_existing_relative_target(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "target.md").write_text("# Target\n", encoding="utf-8")
    (docs / "source.md").write_text("[target](target.md#section)\n", encoding="utf-8")

    errors = check_markdown_links(tmp_path, [Path("docs/source.md")])

    assert errors == []


def test_markdown_link_check_reports_missing_target_without_echoing_link(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("[private label](missing.md)\n", encoding="utf-8")

    errors = check_markdown_links(tmp_path, [Path("README.md")])

    assert errors == ["README.md:1: missing link target"]


def test_secret_check_reports_location_without_echoing_value(tmp_path: Path) -> None:
    value = "sk-" + "A" * 32
    config = tmp_path / "config.txt"
    config.write_text(f"TOKEN={value}\n", encoding="utf-8")

    errors = check_secret_shapes(tmp_path, [Path("config.txt")])

    assert errors == ["config.txt:1: possible long sk credential"]
    assert value not in errors[0]


def test_secret_check_ignores_short_fixture_identifier(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"key": "sk-export_old_1_1"}\n', encoding="utf-8")

    errors = check_secret_shapes(tmp_path, [Path("fixture.json")])

    assert errors == []


def test_publication_check_rejects_private_paths_without_echoing_content(tmp_path: Path) -> None:
    path = Path("docs/datasets/frozen.md")
    (tmp_path / path.parent).mkdir(parents=True)
    (tmp_path / path).write_text("private fixture details\n", encoding="utf-8")

    errors = check_publication_boundary([path])

    assert errors == ["docs/datasets/frozen.md: path is outside the public product boundary"]
    assert "private fixture details" not in errors[0]


def test_publication_check_rejects_new_jsonl_without_explicit_review(tmp_path: Path) -> None:
    path = Path("benchmarks/new-fixture.jsonl")
    (tmp_path / path.parent).mkdir(parents=True)
    (tmp_path / path).write_text("{}\n", encoding="utf-8")

    assert check_publication_boundary([path]) == [
        "benchmarks/new-fixture.jsonl: dataset requires an explicit publication review"
    ]


def test_publication_check_allows_public_fixture(tmp_path: Path) -> None:
    path = Path("benchmarks/ctxttlbench.jsonl")
    (tmp_path / path.parent).mkdir(parents=True)
    (tmp_path / path).write_text("{}\n", encoding="utf-8")

    assert check_publication_boundary([path]) == []


def test_doc_locale_check_accepts_complete_locale_packs(tmp_path: Path) -> None:
    (tmp_path / "docs" / "zh-CN").mkdir(parents=True)
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "docs" / "zh-CN" / "guide.md").write_text("# 指南\n", encoding="utf-8")
    (tmp_path / "docs" / "locales.toml").write_text(
        'schema_version = 1\ndefault_locale = "en"\nlocales = ["en", "zh-CN"]\n'
        '[[pages]]\nid = "guide"\n[pages.paths]\n'
        'en = "docs/guide.md"\nzh-CN = "docs/zh-CN/guide.md"\n',
        encoding="utf-8",
    )

    assert check_doc_locales(tmp_path, Path("docs/locales.toml")) == []


def test_doc_locale_check_reports_missing_locale_without_page_content(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("private prose\n", encoding="utf-8")
    (tmp_path / "docs" / "locales.toml").write_text(
        'schema_version = 1\ndefault_locale = "en"\nlocales = ["en", "zh-CN"]\n'
        '[[pages]]\nid = "guide"\n[pages.paths]\nen = "docs/guide.md"\n',
        encoding="utf-8",
    )

    errors = check_doc_locales(tmp_path, Path("docs/locales.toml"))

    assert errors == ["docs/locales.toml: page guide misses locales ['zh-CN']"]
    assert "private prose" not in errors[0]
