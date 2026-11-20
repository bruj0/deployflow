"""Tests for the documentation site generator.

The generator rewrites every link in every document it assembles. A mistake
there is silent: the site still builds, and the broken link shows up as a
404 that nobody sees until a reader clicks it. These tests check the three
things that can go wrong — a link to the wrong place, a link that points
nowhere, and a link inside a code sample that must not be touched.
"""

from __future__ import annotations

import fnmatch
import functools
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


class TestDiagramAssets:
    """The diagram renderer and its viewer are vendored, not fetched.

    Three failures hide behind a site that still builds: a script that loads
    before the library it uses, a Mermaid bundle that quietly changes, and a
    Mermaid that measures a label box too narrow for its text. All three
    render a page that looks nearly right.
    """

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def site_config():
        import yaml

        class Loader(yaml.SafeLoader):
            """MkDocs uses a Python tag for a fence formatter; keep it as text."""

        Loader.add_multi_constructor(
            "tag:yaml.org,2002:python/name:", lambda loader, suffix, node: suffix
        )
        return yaml.load(
            (REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=Loader
        )

    def test_the_zoom_plugin_is_gone(self):
        """A plugin owns the markup it wraps, so the viewer must not be one."""
        names = [
            name for name in self.site_config().get("plugins", []) if isinstance(name, str)
        ]
        assert "panzoom" not in names

    def test_the_vendor_is_not_declared(self):
        declared = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "mkdocs-panzoom-plugin" not in declared

    def test_the_scripts_load_in_order(self):
        """A script that runs before its library finds no global."""
        scripts = self.site_config().get("extra_javascript", [])
        assert scripts == [
            "media/mermaid.min.js",
            "media/mermaid-theme-patch.js",
            "media/panzoom.min.js",
            "media/diagram-zoom.js",
        ], scripts

    def test_the_stylesheets_load_in_order(self):
        assert self.site_config().get("extra_css", []) == [
            "media/mermaid-card.css",
            "media/diagram-zoom.css",
        ]

    def test_mermaid_is_pinned_to_the_documented_build(self):
        import hashlib

        bundle = REPO_ROOT / "docs" / "media" / "mermaid.min.js"
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert digest == "a8a6a6f805b074a8261a2825c86f99c8054acb3b35f50bb7e24a4f9f2c3c3b9f"

    def test_every_vendored_file_keeps_its_licence(self):
        media = REPO_ROOT / "docs" / "media"
        for bundle in ("mermaid.min.js", "panzoom.min.js"):
            licence = media / (bundle.replace(".min.js", ".LICENSE.txt"))
            assert licence.is_file(), licence

    def test_every_documented_application_has_a_page(self):
        """An application with a README but no page is invisible on the site."""
        documented = {
            p.parent.name
            for p in (REPO_ROOT / "apps").glob("*/README.md")
        }
        pages = {
            path.split("/")[-1][:-3]
            for path in generator.PAGES.values()
            if path.startswith("apps/")
        }
        missing = sorted(documented - pages)
        assert not missing, missing


class TestDiagramLabels:
    """A Mermaid label box must be at least as wide as the text in it.

    Mermaid 12 lowered the default `wrappingWidth` from 200 to 120, and
    Material's own theme CSS forces `overflow: visible` on the label's
    foreign object. A box narrower than its text therefore spills the text
    out of the node and over its neighbour, and nothing in the build says
    so.

    The fix is in the diagram source, so the check is on the source: every
    flowchart that draws a node label carries the width it needs.
    """

    BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)

    @pytest.mark.parametrize(
        "document",
        ["docs/architecture.md", "docs/rendering.md", "essay/software-delivery-needs-a-harness.md"],
    )
    def test_a_flowchart_declares_a_label_width(self, document):
        text = (REPO_ROOT / document).read_text(encoding="utf-8")
        blocks = [block for block in self.BLOCK.findall(text) if "flowchart" in block]
        for block in blocks:
            assert "wrappingWidth: 200" in block, f"{document} has a flowchart without a label width"

    def test_no_flowchart_is_left_at_the_narrower_default(self):
        """`wrappingWidth` absent means Mermaid 12 measures at 120."""
        offenders = []
        for document in sorted(REPO_ROOT.glob("docs/*.md")) + sorted(REPO_ROOT.glob("essay/*.md")):
            text = document.read_text(encoding="utf-8")
            for block in self.BLOCK.findall(text):
                if "flowchart" in block and "wrappingWidth" not in block:
                    offenders.append(document.name)
        assert not offenders, offenders


@pytest.fixture(scope="module")
def assembled(tmp_path_factory):
    output = tmp_path_factory.mktemp("site_src")
    subprocess.run(
        ["python3", str(GENERATOR_PATH), "--output", str(output), "--clean"],
        cwd=REPO_ROOT,
        check=True,
    )
    return output


class TestWorkflowPaths:
    """Every source the generator reads must trigger a site rebuild.

    A `paths:` filter that misses a source is silent: the workflow does not
    run, the site keeps the old page, and nothing fails. Both triggers are
    checked separately, because a filter on the pull-request trigger does not
    help a push to `main`.
    """

    @pytest.fixture(scope="module")
    def triggers(self):
        import re

        workflow = (REPO_ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
        found = {}
        for name in ("push", "pull_request"):
            block = re.search(
                rf"^\s*{name}:\s*$.*?(?=^\s*workflow_dispatch:|\Z)",
                workflow,
                re.MULTILINE | re.DOTALL,
            )
            assert block, f"no {name} trigger in the workflow"
            found[name] = re.findall(r'^\s*-\s*"([^"]+)"\s*$', block.group(0), re.MULTILINE)
        return found

    @pytest.mark.parametrize("trigger", ["push", "pull_request"])
    def test_every_source_is_covered_by_a_path_pattern(self, triggers, trigger):
        patterns = triggers[trigger]
        sources = set(generator.PAGES) | set(generator.ASSETS)
        uncovered = sorted(
            source
            for source in sources
            if not any(fnmatch.fnmatch(source, pattern) for pattern in patterns)
        )
        assert not uncovered, f"the {trigger} trigger would not rebuild for: {uncovered}"

    @pytest.mark.parametrize("trigger", ["push", "pull_request"])
    def test_the_workflow_mentions_the_generator(self, triggers, trigger):
        assert "scripts/build_site_docs.py" in triggers[trigger]

    @pytest.mark.parametrize("trigger", ["push", "pull_request"])
    def test_the_workflow_mentions_the_root_documents(self, triggers, trigger):
        for document in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md"):
            assert document in triggers[trigger], document


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