#!/usr/bin/env python3
"""Assemble the MkDocs source tree for the DeployFlow documentation site.

The repository keeps its documentation where it belongs — next to the code it
describes. That layout is right for GitHub, where a reader lands on a file and
follows relative links. It is wrong for a site, where every page has a URL and
links have to survive being moved.

This script is the adapter between the two. It reads the repository's Markdown
files, rewrites the links that only make sense inside a checkout, and writes
them into a generated tree (`site_src/`) that MkDocs builds. Nothing under
`site_src/` is committed: it is a build artifact, like `site/`.

Two kinds of link need rewriting:

- a link to another document in the repository becomes a link to that
  document's page in the site;
- a link to a source file, a directory or an asset becomes either a local
  asset path or an absolute URL to GitHub.

Run it with `python scripts/build_site_docs.py`, or let the CI job run it
before `mkdocs build`.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = REPO_ROOT / "site_src"

GITHUB_REPO = "https://github.com/bruj0/deployflow"
GITHUB_BLOB = f"{GITHUB_REPO}/blob/main"
GITHUB_TREE = f"{GITHUB_REPO}/tree/main"

# ---------------------------------------------------------------------------
# The page map. Source path (repository-relative) -> site path (under site_src)
# ---------------------------------------------------------------------------

PAGES: dict[str, str] = {
    "essay/software-delivery-needs-a-harness.md": "index.md",
    "README.md": "project/readme.md",
    "AGENTS.md": "project/agents.md",
    "CONTRIBUTING.md": "project/contributing.md",
    "SECURITY.md": "project/security.md",
    ".agents/skills/deployflow-add-app/SKILL.md": "project/add-app-skill.md",
    "docs/README.md": "reference/index.md",
    "docs/overview.md": "reference/overview.md",
    "docs/architecture.md": "reference/architecture.md",
    "docs/targets.md": "reference/targets.md",
    "docs/configuration.md": "reference/configuration.md",
    "docs/build-pipeline.md": "reference/build-pipeline.md",
    "docs/rendering.md": "reference/rendering.md",
    "docs/secrets.md": "reference/secrets.md",
    "docs/site.md": "reference/site.md",
    "apps/example-app/README.md": "apps/example-app.md",
    "apps/omniroute/README.md": "apps/omniroute.md",
    "apps/omniroute-release/README.md": "apps/omniroute-release.md",
    "apps/builder/README.md": "apps/builder.md",
    "apps/omniroute/docs/BUILDING-FROM-SOURCE.md": "apps/omniroute-building-from-source.md",
}

# A link to a directory means "the README of that directory" when there is one.
DIRECTORY_DOCS: dict[str, str | None] = {
    "apps/example-app": "apps/example-app.md",
    "apps/omniroute": "apps/omniroute.md",
    "apps/omniroute-release": "apps/omniroute-release.md",
    "apps/builder": "apps/builder.md",
    "docs": "reference/index.md",
}

# Static files copied into the site and served as-is.
ASSETS: dict[str, str] = {
    "assets/logo.png": "assets/logo.png",
    "assets/logo-icon.png": "assets/logo-icon.png",
    "assets/logo-wordmark.png": "assets/logo-wordmark.png",
    "docs/diagrams/deployflow-workflow.png": "assets/diagrams/deployflow-workflow.png",
    "docs/diagrams/deployflow-workflow.html": "assets/diagrams/deployflow-workflow.html",
    "docs/diagrams/deployflow-anatomy.png": "assets/diagrams/deployflow-anatomy.png",
    "docs/diagrams/deployflow-anatomy.html": "assets/diagrams/deployflow-anatomy.html",
    "docs/diagrams/deployflow-current-state.png": "assets/diagrams/deployflow-current-state.png",
    "docs/diagrams/deployflow-current-state.html": "assets/diagrams/deployflow-current-state.html",
}

# Resolved repository paths that live outside this repository.
EXTERNAL_LINKS: dict[str, str] = {
    "engineer-expectations-before-and-after-genai/engineer-expectations-before-and-after-genai.md": (
        "https://bruj0.github.io/engineer-expectations-after-genai/"
    ),
}

MARKDOWN_LINK = re.compile(r"(\]\()([^)\s]+)([^)]*\))")
HTML_ATTR = re.compile(r"(\s(?:href|src)=\")([^\"]+)(\")")


def split_target(target: str) -> tuple[str, str]:
    """Split a link target into its path and its anchor/query tail."""
    for separator in ("#", "?"):
        index = target.find(separator)
        if index != -1:
            return target[:index], target[index:]
    return target, ""


def resolve(source: str, target: str) -> str:
    """Resolve a link target relative to the file that contains it."""
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source)
    return posixpath.normpath(posixpath.join(base, target))


def to_github(path: str) -> str:
    """Absolute URL for a repository file or directory."""
    clean = path.rstrip("/")
    if (REPO_ROOT / clean).is_dir():
        return f"{GITHUB_TREE}/{clean}"
    return f"{GITHUB_BLOB}/{clean}"


def site_relative(from_page: str, to_page: str) -> str:
    """Relative path from one generated page to another, in site_src terms."""
    return posixpath.relpath(to_page, posixpath.dirname(from_page))


def asset_relative(from_page: str, to_asset: str) -> str:
    """Relative URL from a generated page to a copied asset.

    MkDocs rewrites Markdown links against the source tree, so a Markdown
    link is relative to the directory holding the page.
    """
    return site_relative(from_page, to_asset)


def html_asset_relative(from_page: str, to_asset: str) -> str:
    """Relative URL for an asset referenced from raw HTML.

    MkDocs does not rewrite `src` and `href` attributes inside HTML. The
    browser resolves them against the served URL, and with directory URLs
    that is the page's own directory — one level deeper than the source
    tree — so the path needs one more `..` than a Markdown link.
    """
    page_dir = posixpath.join(posixpath.dirname(from_page), posixpath.basename(from_page))
    return posixpath.relpath(to_asset, page_dir)


GITHUB_URL = re.compile(
    r"^https?://github\.com/bruj0/deployflow/(?:blob|tree)/main/(?P<path>[^?#]+)(?P<tail>[?#].*)?$"
)


def rewrite_target(source: str, page: str, target: str, mode: str = "markdown") -> str | None:
    """Return the rewritten target, or None to leave it untouched.

    `mode` is "markdown" or "html" and only affects how an asset path is
    expressed: Markdown links are resolved by MkDocs against the source
    tree, raw HTML attributes are resolved by the browser against the served
    URL.
    """
    if not target:
        return None

    # A link to this repository on GitHub becomes a link to the site page
    # when that document is part of the site.
    github = GITHUB_URL.match(target)
    if github:
        path = github.group("path")
        tail = github.group("tail") or ""
        if path in PAGES:
            return site_relative(page, PAGES[path]) + tail
        if path.rstrip("/") in DIRECTORY_DOCS:
            target_page = DIRECTORY_DOCS[path.rstrip("/")]
            if target_page:
                return site_relative(page, target_page) + tail
        return target

    if target.startswith(("http://", "https://", "mailto:", "tel:", "//", "#")):
        return None

    path, tail = split_target(target)
    if not path:
        return None

    resolved = resolve(source, path)

    if resolved in EXTERNAL_LINKS:
        return EXTERNAL_LINKS[resolved] + tail

    # A document in the repository becomes a page in the site.
    if resolved in PAGES:
        return site_relative(page, PAGES[resolved]) + tail

    # A directory link means the README of that directory.
    directory = resolved.rstrip("/")
    if directory in DIRECTORY_DOCS:
        target_page = DIRECTORY_DOCS[directory]
        if target_page:
            return site_relative(page, target_page) + tail
        return to_github(directory) + tail

    # A copied asset stays local.
    if resolved in ASSETS:
        relative = (
            asset_relative(page, ASSETS[resolved])
            if mode == "markdown"
            else html_asset_relative(page, ASSETS[resolved])
        )
        return relative + tail

    # Anything else in the repository is source code: link to GitHub.
    if (REPO_ROOT / resolved).exists():
        return to_github(resolved) + tail

    return None


FENCE_LINE = re.compile(r"^(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter.

    MkDocs has no frontmatter support, and the rendered page should not open
    with a block of metadata. The skill's frontmatter is metadata for the
    agent runtime, not for a reader.
    """
    return FRONTMATTER.sub("", text, count=1).lstrip("\n")


def rewrite_document(source: str, page: str, text: str) -> str:
    """Rewrite the links in one document.

    Fenced blocks are copied verbatim: a link inside a code sample is an
    example, not a reference to follow.
    """
    text = strip_frontmatter(text)
    lines = text.splitlines(keepends=True)
    rebuilt: list[str] = []
    open_fence: str | None = None

    for line in lines:
        match = FENCE_LINE.match(line)
        if open_fence is None:
            if match:
                open_fence = match.group("fence")
                rebuilt.append(line)
                continue
            rebuilt.append(rewrite_line(source, page, line))
            continue

        rebuilt.append(line)
        if match and match.group("fence")[0] == open_fence[0] and len(match.group("fence")) >= len(open_fence):
            open_fence = None

    return "".join(rebuilt)


def rewrite_line(source: str, page: str, line: str) -> str:
    """Rewrite the links in one line of prose."""

    def markdown_sub(match: re.Match[str]) -> str:
        new = rewrite_target(source, page, match.group(2))
        if new is None:
            return match.group(0)
        return match.group(1) + new + match.group(3)

    def html_sub(match: re.Match[str]) -> str:
        new = rewrite_target(source, page, match.group(2), mode="html")
        if new is None:
            return match.group(0)
        return match.group(1) + new + match.group(3)

    return HTML_ATTR.sub(html_sub, MARKDOWN_LINK.sub(markdown_sub, line))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(SITE_SRC), help="where to write the site source tree")
    parser.add_argument("--clean", action="store_true", help="remove the output tree first")
    args = parser.parse_args()

    output = Path(args.output)
    if args.clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    missing = [source for source in PAGES if not (REPO_ROOT / source).is_file()]
    if missing:
        for source in missing:
            print(f"missing source document: {source}", file=sys.stderr)
        return 1

    for source, page in PAGES.items():
        text = strip_frontmatter((REPO_ROOT / source).read_text(encoding="utf-8"))
        destination = output / page
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rewrite_document(source, page, text), encoding="utf-8")

    for source, asset in ASSETS.items():
        origin = REPO_ROOT / source
        if not origin.is_file():
            print(f"missing asset: {source}", file=sys.stderr)
            return 1
        destination = output / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)

    print(f"wrote {len(PAGES)} pages and {len(ASSETS)} assets to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
