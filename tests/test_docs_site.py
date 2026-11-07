from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = DOCS / "index.html"
README = ROOT / "README.md"
WHEEL_URL = (
    "https://github.com/rinch-wu/amz-leak-audit/releases/download/"
    "v0.1.0/amzleak-0.1.0-py3-none-any.whl"
)
INSTALL_COMMAND = f'pip install "{WHEEL_URL}"'
SHA256 = "3e50de0a250a679f3ab43b6eb63f569c23541dcb42ce0a90d32a66962bd0e4b4"


def read_index() -> str:
    return INDEX.read_text(encoding="utf-8")


def read_readme() -> str:
    return README.read_text(encoding="utf-8")


def test_index_exists_and_has_exactly_one_h1() -> None:
    assert INDEX.is_file()
    html = read_index()
    assert html.count("<h1") == 1
    assert "在你自己电脑上，查出亚马逊三份报表里漏掉的钱" in html


def test_tool_guide_has_exactly_three_steps_and_copy_buttons() -> None:
    html = read_index()
    assert 'id="tool-guide"' in html
    assert "💡 功能介绍与极速使用指南" in html
    assert html.count('class="tg-step"') == 3
    assert html.count('class="copy-btn"') == 3
    assert html.count('data-copy-target="') == 3


def test_index_has_no_insecure_http_urls() -> None:
    assert read_index().count("http://") == 0


def test_index_uses_correct_public_identity() -> None:
    html = read_index()
    assert "HongfeiWu001" not in html
    assert "rinch-wu.github.io/amz-leak-audit" in html


def test_index_contains_release_wheel_and_real_sha256() -> None:
    html = read_index()
    assert WHEEL_URL in html
    assert SHA256 in html
    assert "sha256sum -c -" in html


def test_index_seo_and_social_metadata() -> None:
    html = read_index()
    assert '<html lang="zh-CN">' in html
    assert "<title>amzleak · 亚马逊报表本地漏损审计 CLI</title>" in html
    assert '<meta name="description"' in html
    assert '<link rel="canonical" href="https://rinch-wu.github.io/amz-leak-audit/">' in html
    for prop in ("og:title", "og:description", "og:url", "og:type"):
        assert f'property="{prop}"' in html
    assert 'property="og:type" content="website"' in html


def test_index_is_self_contained_and_has_six_faqs() -> None:
    html = read_index()
    assert "<script src" not in html
    assert "<img" not in html
    assert 'rel="stylesheet"' not in html
    assert "@import" not in html
    assert "url(" not in html
    assert html.count("<details>") == 6
    assert "<table>" in html
    assert "@media (max-width: 720px)" in html
    assert "border-radius: 14px" in html


def test_readme_public_identity_urls_and_scope_warning() -> None:
    readme = read_readme()
    assert "HongfeiWu001" not in readme
    assert "rinch-wu/amz-leak-audit" in readme
    assert "被压死资金" in readme
    assert "不可与可追回现金相加" in readme
    assert "## 3 步上手" in readme
    assert readme.count("# amzleak · 亚马逊报表本地漏损审计 CLI") == 1
    assert len(re.findall(r"^\d+\. ", readme, re.MULTILINE)) == 3


def test_install_command_is_identical_in_readme_and_landing_page() -> None:
    assert INSTALL_COMMAND in read_readme()
    assert INSTALL_COMMAND in read_index()


def test_readme_contains_real_sha256() -> None:
    assert SHA256 in read_readme()


def test_sitemap_parses_and_robots_references_it() -> None:
    tree = ET.parse(DOCS / "sitemap.xml")
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = tree.getroot().findall("sm:url/sm:loc", namespace)
    assert len(locations) == 1
    assert locations[0].text == "https://rinch-wu.github.io/amz-leak-audit/"
    robots = (DOCS / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert "Sitemap: https://rinch-wu.github.io/amz-leak-audit/sitemap.xml" in robots
    assert (DOCS / ".nojekyll").is_file()


def test_pyproject_has_project_urls_and_zero_runtime_dependencies() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'Homepage = "https://rinch-wu.github.io/amz-leak-audit/"' in pyproject
    assert 'Repository = "https://github.com/rinch-wu/amz-leak-audit"' in pyproject
    assert "\ndependencies = []\n" in pyproject
    assert '"amazon-fba"' in pyproject
    assert '"leak-audit"' in pyproject


def test_readme_and_landing_page_share_documented_commands() -> None:
    readme = read_readme()
    html = read_index()
    shared_commands = [
        "amzleak sample -o demo",
        'amzleak ledger --settlement demo/settlement-sample.tsv --inventory demo/inventory-age-sample.csv --ppc demo/search-term-sample.csv',
        'amzleak ledger --settlement "path/to/settlement.tsv" --inventory "path/to/inventory-age.csv" --ppc "path/to/search-term.csv"',
    ]
    for command in shared_commands:
        assert command in readme
        assert command in html
