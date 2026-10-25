# The documentation site

The repository's documentation lives next to the code it describes. That is
right for GitHub: a reader lands on a file and follows relative links. It is
wrong for a site, where every page has a URL and a link has to survive being
moved.

`scripts/build_site_docs.py` is the adapter. It reads the repository's own
Markdown, rewrites the links that only make sense inside a checkout, and
writes the result into `site_src/`. MkDocs builds that tree into `site/`.

Neither `site_src/` nor `site/` is committed. The sources are the files the
generator reads; the outputs are build artifacts.

## Build it locally

```bash
uv venv -p 3.12 .venv-docs
uv pip install --python .venv-docs/bin/python -e ".[docs]"
.venv-docs/bin/python scripts/build_site_docs.py --clean
.venv-docs/bin/python -m mkdocs build --strict --site-dir site
```

Serve it with a live reload while writing:

```bash
.venv-docs/bin/python -m mkdocs serve --dev-addr 127.0.0.1:8899
```

The site is served under `/deployflow/` locally, because `site_url` names the
GitHub Pages path. That prefix is not part of the site: it is where Pages
will mount it.

## What the generator does

Three kinds of link change when a document moves from the repository to the
site.

| In the repository | On the site |
|---|---|
| `docs/overview.md` | a link to the page `reference/overview.md` |
| `src/appctl/cli.py` | an absolute URL to the file on GitHub |
| `assets/logo.png` | a relative path to the copied asset |

The examples in that table are code spans, not links: they show what a link
target looks like before and after the rewrite, and a link to a target that
does not exist would be a dead link on the site.

The page map lives in `PAGES` in the generator: repository path → site path.
Adding a document to the site means adding a row there and a line in the
`nav` block of `mkdocs.yml`.

Two details are easy to get wrong.

**Markdown links and raw HTML are resolved differently.** MkDocs rewrites a
Markdown link against the source tree. It does not touch the `src` and
`href` attributes inside raw HTML, and the browser resolves those against the
served URL — one directory deeper, because the page is served at
`<page>/`. The generator emits a different relative path for each case. An
image referenced with `<img src="...">` that renders in the repository can be
broken on the site if this is not handled.

**A fenced block is copied verbatim.** A link inside a code sample is an
example, not a reference to follow.

## The essay

`essay/software-delivery-needs-a-harness.md` is the home page. It is a copy of
the essay, kept in this repository so the site is self-contained: the site
build must not depend on a file that lives somewhere else.

The essay links to the repository's own files by absolute GitHub URL. The
generator rewrites those to site pages when the target is part of the site, so
a reader can stay on the site, and leaves the rest as GitHub links.

## Diagrams

Mermaid blocks are rendered by Material's lazy loader, which replaces a
`<pre class="mermaid">` element with the rendered SVG. The `mermaid_fences`
package supplies that element — the stock superfences formatter wraps the
diagram in `<code>`, and Mermaid cannot parse through the wrapper.

The renderer loads Mermaid from a CDN at runtime, so a diagram needs the
network. Every diagram in this repository is also readable as text, which is
what a reader without the CDN gets.

## Publishing

The `docs` workflow builds the site on every change to the documentation and
deploys it to the `gh-pages` branch on a push to `main`. Publishing requires
the repository to have Pages enabled with the branch source set to `gh-pages`
and the `/ (root)` folder.

The site is at <https://bruj0.github.io/deployflow>.
