"""Tests for the documentation site generator.

The generator rewrites every link in every document it assembles. A mistake
there is silent: the site still builds, and the broken link shows up as a
404 that nobody sees until a reader clicks it. These tests check the three
things that can go wrong — a link to the wrong place, a link that points
nowhere, and a link inside a code sample that must not be touched.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "build_site_docs.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("build_site_docs", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_site_docs"] = module
    spec.loader.exec_module(module)
    return module


generator = load_generator()


class TestLinkResolution:
    def test_a_document_link_becomes_a_page_link(self):
        result = generator.rewrite_target("README.md", "project/readme.md", "docs/overview.md")
        assert result == "../reference/overview.md"

    def test_a_source_file_link_becomes_a_github_url(self):
        result = generator.rewrite_target("README.md", "project/readme.md", "src/appctl/cli.py")
        assert result == "https://github.com/bruj0/deployflow/blob/main/src/appctl/cli.py"

    def test_a_directory_link_becomes_a_github_tree_url(self):
        result = generator.rewrite_target("README.md", "project/readme.md", "apps/")
        assert result == "https://github.com/bruj0/deployflow/tree/main/apps"

    def test_a_directory_with_a_readme_becomes_its_page(self):
        result = generator.rewrite_target("README.md", "project/readme.md", "apps/builder/")
        assert result == "../apps/builder.md"

    def test_an_absolute_github_link_to_a_site_document_becomes_a_page_link(self):
        result = generator.rewrite_target(
            "essay/software-delivery-needs-a-harness.md",
            "index.md",
            "https://github.com/bruj0/deployflow/blob/main/apps/omniroute/README.md",
        )
        assert result == "apps/omniroute.md"

    def test_an_absolute_github_link_to_a_source_file_is_left_alone(self):
        target = "https://github.com/bruj0/deployflow/blob/main/apps/omniroute/tasks/main.yml"
        assert generator.rewrite_target("README.md", "project/readme.md", target) == target

    def test_an_anchor_is_left_alone(self):
        assert generator.rewrite_target("docs/overview.md", "reference/overview.md", "#data-flow") is None

    def test_an_external_url_is_left_alone(self):
        assert generator.rewrite_target("README.md", "project/readme.md", "https://example.com/x") is None

    def test_an_anchor_on_a_page_link_survives(self):
        result = generator.rewrite_target(
            "docs/configuration.md", "reference/configuration.md", "build-pipeline.md#kind-toolchain"
        )
        assert result == "build-pipeline.md#kind-toolchain"

    def test_a_github_link_to_the_earlier_essay_points_at_its_own_site(self):
        result = generator.rewrite_target(
            "essay/software-delivery-needs-a-harness.md",
            "index.md",
            "../engineer-expectations-before-and-after-genai/engineer-expectations-before-and-after-genai.md",
        )
        assert result == "https://bruj0.github.io/engineer-expectations-after-genai/"


class TestFencedBlocks:
    def test_a_link_inside_a_code_sample_is_not_rewritten(self):
        text = "```bash\ncat docs/overview.md\n```\n"
        result = generator.rewrite_document("README.md", "project/readme.md", text)
        assert "cat docs/overview.md" in result

    def test_prose_outside_the_fence_is_rewritten(self):
        text = "See [overview](docs/overview.md).\n\n```bash\ncat docs/overview.md\n```\n"
        result = generator.rewrite_document("README.md", "project/readme.md", text)
        assert "[overview](../reference/overview.md)" in result
        assert "cat docs/overview.md" in result


class TestFrontmatter:
    def test_frontmatter_is_removed(self):
        text = "---\nname: skill\ndescription: x\n---\n\n# Heading\n"
        result = generator.rewrite_document(
            ".agents/skills/deployflow-add-app/SKILL.md", "project/add-app-skill.md", text
        )
        assert result.startswith("# Heading")


@pytest.fixture(scope="module")
def assembled(tmp_path_factory):
    output = tmp_path_factory.mktemp("site_src")
    subprocess.run(
        ["python3", str(GENERATOR_PATH), "--output", str(output), "--clean"],
        cwd=REPO_ROOT,
        check=True,
    )
    return output


class TestAssembledSite:
    def test_every_page_in_the_map_is_written(self, assembled):
        for page in generator.PAGES.values():
            assert (assembled / page).is_file(), page

    def test_every_asset_is_copied(self, assembled):
        for asset in generator.ASSETS.values():
            assert (assembled / asset).is_file(), asset

    def test_no_document_still_points_at_a_repository_relative_path(self, assembled):
        """A relative link that survived the rewrite would 404 on the site."""
        pattern = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")
        offenders = []
        for page in assembled.rglob("*.md"):
            for target in pattern.findall(page.read_text(encoding="utf-8")):
                path, _, _ = target.partition("#")
                if not path or path.startswith(("http", "/")):
                    continue
                resolved = (page.parent / path).resolve()
                if resolved.is_dir():
                    resolved = resolved / "index.md"
                if not resolved.exists():
                    offenders.append(f"{page.relative_to(assembled)} -> {target}")
        assert not offenders, offenders