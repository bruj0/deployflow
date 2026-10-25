"""
Custom superfences formatter for MkDocs Material 9.x Mermaid rendering.

Material 9.x lazy-loads mermaid@11 by scanning for `pre.mermaid` elements.
- fence_code_format → <pre><code> wrapper — Mermaid cannot parse through it
- fence_div_format  → <div> — Material's bundle never picks up div.mermaid
This formatter emits bare <pre class="mermaid"> which is what Material expects.
"""
from pymdownx.superfences import _escape


def fence_pre_format(source, language, class_name, options, md, **kwargs):
    """Emit a bare <pre class="mermaid"> block — no inner <code> wrapper."""
    classes = kwargs.get("classes", [])
    id_value = kwargs.get("id_value", "")
    attrs = kwargs.get("attrs", {})

    if class_name:
        classes = [class_name] + list(classes)

    id_str = f' id="{id_value}"' if id_value else ""
    class_str = ' class="{}"'.format(" ".join(classes)) if classes else ""
    attr_str = (" " + " ".join(f'{k}="{v}"' for k, v in attrs.items())) if attrs else ""

    return "<pre{}{}{}>{}</pre>".format(id_str, class_str, attr_str, _escape(source))
