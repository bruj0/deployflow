/**
 * Pan and zoom for the Mermaid diagrams.
 *
 * # Why this exists
 *
 * Several of the diagrams are wider than the content column, and a reader on
 * a narrow screen has to scroll sideways to reach the right-hand end. A
 * diagram that describes a data flow or a sequence is naturally wide, and
 * narrowing it to fit would make it unreadable rather than tidy.
 *
 * So a wide diagram is made readable by letting the reader zoom into it,
 * rather than by shrinking it.
 *
 * # Why a full-screen viewer
 *
 * The first version of this script opened a panel and zoomed by changing the
 * SVG's `viewBox`. That is exact - the geometry never distorts - but it is
 * awkward to drive: there is no panning, the zoom step is coarse, and the
 * reader has to find a small button to make anything happen at all.
 *
 * This version opens the diagram in a full-screen viewer built on
 * `@panzoom/panzoom` (vendored next to this file as `panzoom.min.js`, MIT
 * licensed - see `panzoom.LICENSE.txt`). It is the arrangement the
 * established diagram viewers use:
 *
 *   - the diagram opens in a full-screen overlay, so there is room to work;
 *   - **drag to pan**, with the mouse or a finger;
 *   - **the wheel zooms**, anchored under the pointer;
 *   - **pinch to zoom** on a touch screen;
 *   - a **3x3 control grid** for readers who would rather press a button
 *     than drag;
 *   - **keyboard shortcuts**: Escape closes, `+`/`-` zoom, `0` resets, and
 *     the arrow keys pan.
 *
 * # Why the shadow root is intercepted
 *
 * Material renders a Mermaid diagram into a **closed** shadow root:
 *
 *   const root = host.attachShadow({ mode: "closed" });
 *   root.innerHTML = svg;
 *
 * A closed shadow root is not reachable from the document. `host.shadowRoot`
 * is `null`, so the rendered SVG cannot be styled, measured, copied or moved
 * by a stylesheet or a query - the diagram is a sealed box.
 *
 * The seal is opened at the source: `attachShadow` is wrapped *before*
 * Material's asynchronous render runs, and the wrapper remembers the root it
 * created. The wrapper stays installed, because Material's render can happen
 * at any point in the page's life and there is no moment at which it is
 * safe to remove it.
 */
(function () {
  "use strict";

  const Panzoom = window.Panzoom;
  if (typeof Panzoom !== "function") {
    // The vendored library is missing. Say so once, in the console, and
    // leave the page alone rather than throwing on every diagram.
    console.warn(
      "[keyspout] @panzoom/panzoom is not loaded; the diagram viewer is disabled.",
    );
    return;
  }

  /**
   * Remember the shadow root Material creates.
   *
   * The map hangs off the wrapper rather than off this closure, so a second
   * copy of this script finds the roots the first copy collected instead of
   * waiting for a render that will never come.
   */
  const roots = new WeakMap();
  if (!Element.prototype.attachShadow.__kspWrapped) {
    const attachShadow = Element.prototype.attachShadow;
    const wrapped = function (options) {
      const root = attachShadow.call(this, options);
      roots.set(this, root);
      return root;
    };
    wrapped.__kspWrapped = true;
    Element.prototype.attachShadow = wrapped;
  }

  /** The open viewer, or `null` when nothing is open. */
  let viewer = null;

  /**
   * The intrinsic size of a rendered diagram, in diagram units.
   *
   * The intrinsic width is the `viewBox`'s width: the coordinate system the
   * diagram was laid out in. The host's own width is the width the
   * container allowed, which is the *squeezed* width, and comparing against
   * it would say a diagram is narrow when it was only narrowed by the
   * column it happens to sit in.
   */
  function intrinsicSize(source) {
    const viewBox = source.getAttribute("viewBox");
    if (!viewBox) return null;
    const parts = viewBox.trim().split(/[\s,]+/).map(Number);
    if (parts.length !== 4) return null;
    const [, , width, height] = parts;
    if (!(width > 0) || !(height > 0)) return null;
    return { width, height };
  }

  /**
   * A short, human label for the diagram, taken from its heading.
   *
   * The heading carries Material's permalink anchor, and `textContent`
   * includes its `¶`. The anchor is removed from a copy rather than from
   * the page, so the label reads as the heading does without the page
   * losing its permalink.
   */
  function diagramLabel(host) {
    let node = host.previousElementSibling;
    while (node) {
      if (/^H[1-6]$/.test(node.tagName)) {
        const copy = node.cloneNode(true);
        copy.querySelectorAll(".headerlink").forEach((a) => a.remove());
        const text = copy.textContent.replace(/\s+/g, " ").trim();
        if (text) return text;
      }
      node = node.previousElementSibling;
    }
    return "the diagram";
  }

  /* ------------------------------------------------------------------ */
  /* The viewer                                                         */
  /* ------------------------------------------------------------------ */

  /**
   * Build a clamp for one axis.
   *
   * Panzoom will happily move the diagram off the screen, which means it
   * can also lose it. The clamp keeps it in reach: a diagram larger than
   * the viewer is held against the viewer's edges, and a diagram smaller
   * than the viewer is centred.
   *
   * # The arithmetic
   *
   * Panzoom's transform is `scale(s) translate(x, y)`, applied about the
   * element's centre. The element's box starts at the stage's origin, so
   * the drawn left edge of the diagram is
   *
   *   drawn = s * x + (size / 2) * (1 - s)
   *
   * The second term is the element's own centre moving as it scales. A
   * clamp written against `s * x` alone would drift by half the diagram's
   * size, so it is carried through here.
   */
  function makeClamp(size, extent) {
    return function clamp(value, scale) {
      const offset = (size / 2) * (1 - scale);
      const drawn = scale * value + offset;
      const scaled = size * scale;

      let target;
      if (scaled <= extent) {
        // Smaller than the viewer: centre it.
        target = (extent - scaled) / 2;
      } else {
        // Larger than the viewer: hold it against an edge.
        target = Math.min(0, Math.max(extent - scaled, drawn));
      }
      return (target - offset) / scale;
    };
  }

  /**
   * Open a diagram in the full-screen viewer.
   */
  function open(host) {
    if (viewer) return;

    const root = roots.get(host);
    if (!root) return;

    const source = root.querySelector("svg");
    if (!source) return;

    const size = intrinsicSize(source);
    if (!size) return;

    // The SVG is copied into the light DOM. The copy keeps its own viewBox,
    // so it is the same drawing at the same geometry - only the size and
    // the position change.
    const clone = source.cloneNode(true);
    clone.removeAttribute("style");
    clone.setAttribute("width", String(size.width));
    clone.setAttribute("height", String(size.height));
    // Without this, text scaled by a transform is re-laid out and the
    // glyphs drift apart.
    clone.setAttribute("text-rendering", "geometricPrecision");
    clone.style.maxWidth = "none";
    clone.style.width = size.width + "px";
    clone.style.height = size.height + "px";
    clone.style.display = "block";
    // A link inside a diagram stays a link: Panzoom must not treat a
    // pointer-down on it as the start of a drag.
    clone.querySelectorAll("a").forEach((link) => {
      link.classList.add("panzoom-exclude");
    });

    const label = diagramLabel(host);

    const overlay = document.createElement("div");
    overlay.className = "dgz";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", label);

    const stage = document.createElement("div");
    stage.className = "dgz__stage";

    const content = document.createElement("div");
    content.className = "dgz__content";
    content.style.width = size.width + "px";
    content.style.height = size.height + "px";
    content.appendChild(clone);

    stage.appendChild(content);
    overlay.appendChild(stage);
    overlay.appendChild(buildControls());
    overlay.appendChild(buildClose());
    overlay.appendChild(buildLevel());

    document.body.appendChild(overlay);
    document.body.classList.add("dgz--open");

    // The overlay claims to be modal, so the page behind it must not take
    // the keyboard. `inert` is the honest way to say that: it removes the
    // siblings from the tab order and from the accessibility tree without
    // touching their styles or their event handlers.
    const inerted = [];
    for (const child of document.body.children) {
      if (child === overlay) continue;
      if (child.hasAttribute("inert")) continue;
      child.setAttribute("inert", "");
      inerted.push(child);
    }

    // The stage has to be in the document before it can be measured.
    const padding = 32;
    const fit = Math.max(
      0.02,
      Math.min(
        1,
        (stage.clientWidth - padding) / size.width,
        (stage.clientHeight - padding) / size.height,
      ),
    );

    let clampX = makeClamp(size.width, stage.clientWidth);
    let clampY = makeClamp(size.height, stage.clientHeight);

    let panzoom = null;

    /**
     * Draw the diagram, clamped.
     *
     * The transform is written here rather than by Panzoom's own writer so
     * the clamp is applied to what is actually drawn: the diagram is never
     * seen outside the viewer, not even for one frame.
     *
     * The clamped position is then written back into Panzoom, so the
     * library's own state is the position the reader can see. Without
     * that the two drift apart, and a drag that starts at an edge pulls
     * the diagram from a place it was never drawn at. The clamp is
     * idempotent, so the write-back settles on the second pass and the
     * chain stops there.
     */
    function applyTransform(element, values) {
      const scale = values.scale;
      const x = clampX(values.x, scale);
      const y = clampY(values.y, scale);

      element.style.transform =
        "scale(" + scale + ") translate(" + x + "px, " + y + "px)";

      if (panzoom && (x !== values.x || y !== values.y)) {
        panzoom.pan(x, y, { force: true, animate: false });
      }
    }

    panzoom = Panzoom(content, {
      canvas: true,
      cursor: "grab",
      step: 0.3,
      minScale: Math.max(0.02, fit * 0.2),
      maxScale: 8,
      startScale: fit,
      startX: clampX(0, fit),
      startY: clampY(0, fit),
      setTransform: applyTransform,
    });

    const level = overlay.querySelector(".dgz__level__value");

    content.addEventListener("panzoomchange", (event) => {
      if (level) {
        level.textContent =
          Math.round((event.detail.scale / fit) * 100) + "%";
      }
    });

    // The cursor says whether the diagram can be dragged.
    content.addEventListener("panzoomstart", () => {
      stage.style.cursor = "grabbing";
    });
    content.addEventListener("panzoomend", () => {
      stage.style.cursor = "grab";
    });

    /* --- the controls ------------------------------------------------ */

    /**
     * Zoom about the middle of the viewer.
     *
     * `zoom()` alone keeps the translate and lets the scale grow, which
     * slides the diagram away from the reader: what they were looking at
     * drifts off to one side. `zoomToPoint()` anchors the scale on a point,
     * so the middle of the viewer stays the middle of the viewer.
     */
    function zoomBy(factor) {
      const options = panzoom.getOptions();
      const scale = panzoom.getScale();
      const next = Math.min(
        options.maxScale,
        Math.max(options.minScale, scale * factor),
      );
      const box = stage.getBoundingClientRect();
      panzoom.zoomToPoint(
        next,
        { clientX: box.left + box.width / 2, clientY: box.top + box.height / 2 },
        { force: true },
      );
    }

    /**
     * Move the diagram by a number of *screen* pixels.
     *
     * The transform is `scale(s) translate(x, y)`, so a point at `p` is
     * drawn at `s * (p + t)`. Moving the diagram `dx` screen pixels is
     * therefore a translate change of `dx / s`, not `dx`. Getting that
     * wrong makes the pan controls move the diagram by more than they say
     * once it is zoomed.
     */
    function panBy(dx, dy) {
      const scale = panzoom.getScale();
      const current = panzoom.getPan();
      panzoom.pan(current.x + dx / scale, current.y + dy / scale, {
        force: true,
        animate: true,
      });
    }

    const actions = {
      "zoom-in": () => zoomBy(1.25),
      "zoom-out": () => zoomBy(1 / 1.25),
      reset: () => panzoom.reset({ animate: true }),
      "pan-up": () => panBy(0, -80),
      "pan-down": () => panBy(0, 80),
      "pan-left": () => panBy(-80, 0),
      "pan-right": () => panBy(80, 0),
    };

    overlay.addEventListener("click", (event) => {
      const action = event.target.getAttribute("data-dgz");
      if (!action) return;
      event.stopPropagation();
      const handler = actions[action];
      if (handler) handler();
    });

    /* --- the wheel --------------------------------------------------- */

    // The wheel zooms rather than scrolls, anchored under the pointer.
    // The listener is not passive, because the zoom has to suppress the
    // page's own scrolling.
    stage.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        panzoom.zoomWithWheel(event);
      },
      { passive: false },
    );

    /* --- the keyboard ------------------------------------------------ */

    function onKeyDown(event) {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
      ) {
        return;
      }

      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key === "+" || event.key === "=") {
        zoomBy(1.25);
        event.preventDefault();
        return;
      }
      if (event.key === "-" || event.key === "_") {
        zoomBy(1 / 1.25);
        event.preventDefault();
        return;
      }
      if (event.key === "0") {
        actions.reset();
        event.preventDefault();
        return;
      }
      const nudges = {
        ArrowUp: [0, -80],
        ArrowDown: [0, 80],
        ArrowLeft: [-80, 0],
        ArrowRight: [80, 0],
      };
      const nudge = nudges[event.key];
      if (nudge) {
        panBy(nudge[0], nudge[1]);
        event.preventDefault();
      }
    }

    document.addEventListener("keydown", onKeyDown);

    /* --- closing ----------------------------------------------------- */

    // The pointer is tracked in the *capture* phase, so the position is
    // recorded before Panzoom's own handler gets a chance to stop the
    // event's propagation.
    let pressedAt = null;

    stage.addEventListener(
      "pointerdown",
      (event) => {
        pressedAt = { x: event.clientX, y: event.clientY };
      },
      true,
    );

    function onPointerUp(event) {
      {
        if (!pressedAt) return;
        const moved =
          Math.abs(event.clientX - pressedAt.x) +
          Math.abs(event.clientY - pressedAt.y);
        pressedAt = null;
        // A click on the empty part of the viewer closes it, the way a
        // lightbox does. A click that followed a drag does not, and a
        // click on the diagram does not either.
        if (moved > 4) return;
        if (event.target !== stage) return;
        close();
      }
    }
    document.addEventListener("pointerup", onPointerUp, true);

    function onResize() {
      // The viewer's size changed under us. Re-measure and re-clamp, so a
      // diagram that was held against an edge follows the new edge.
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      if (!width || !height) return;
      clampX = makeClamp(size.width, width);
      clampY = makeClamp(size.height, height);
      const scale = panzoom.getScale();
      const current = panzoom.getPan();
      panzoom.pan(current.x, current.y, { force: true, animate: false });
    }

    window.addEventListener("resize", onResize);

    viewer = {
      overlay,
      panzoom,
      dispose() {
        document.removeEventListener("keydown", onKeyDown);
        document.removeEventListener("pointerup", onPointerUp, true);
        window.removeEventListener("resize", onResize);
        panzoom.destroy();
        overlay.remove();
        document.body.classList.remove("dgz--open");
        for (const child of inerted) child.removeAttribute("inert");
      },
    };

    // The close button takes the keyboard, so that a reader who opened the
    // viewer with the keyboard can close it again without hunting.
    const closeButton = overlay.querySelector(".dgz__close");
    if (closeButton) closeButton.focus();
  }

  function close() {
    if (!viewer) return;
    viewer.dispose();
    viewer = null;
  }

  /* ------------------------------------------------------------------ */
  /* The controls markup                                                */
  /* ------------------------------------------------------------------ */

  function buildControls() {
    const grid = document.createElement("div");
    grid.className = "dgz__controls";
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", "Move and zoom the diagram");

    const cells = [
      ["zoom-in", "+", "Zoom in"],
      ["pan-up", "↑", "Pan up"],
      ["zoom-out", "−", "Zoom out"],
      ["pan-left", "←", "Pan left"],
      ["reset", "⤾", "Reset the view"],
      ["pan-right", "→", "Pan right"],
      ["", "", ""],
      ["pan-down", "↓", "Pan down"],
      ["", "", ""],
    ];

    for (const [action, glyph, title] of cells) {
      if (!action) {
        const spacer = document.createElement("span");
        spacer.className = "dgz__cell dgz__cell--empty";
        grid.appendChild(spacer);
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "dgz__cell";
      button.setAttribute("data-dgz", action);
      button.setAttribute("aria-label", title);
      button.setAttribute("title", title);
      button.textContent = glyph;
      grid.appendChild(button);
    }

    return grid;
  }

  function buildClose() {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dgz__close";
    button.setAttribute("data-dgz", "close");
    button.setAttribute("aria-label", "Close the diagram");
    button.setAttribute("title", "Close (Escape)");
    button.textContent = "×";
    button.addEventListener("click", close);
    return button;
  }

  function buildLevel() {
    const level = document.createElement("div");
    level.className = "dgz__level";
    const value = document.createElement("span");
    value.className = "dgz__level__value";
    value.textContent = "100%";
    level.appendChild(value);
    return level;
  }

  /* ------------------------------------------------------------------ */
  /* Putting a way in on every diagram                                   */
  /* ------------------------------------------------------------------ */

  /**
   * Give every diagram a way in.
   *
   * The button sits *above* the diagram rather than over it, so it never
   * covers part of the drawing and never intercepts a click that was meant
   * for the diagram. Clicking the diagram itself also opens the viewer,
   * unless the diagram contains a link.
   */
  function decorate(host) {
    if (host.dataset.dgz === "done") return;

    const root = roots.get(host);
    if (!root) return;

    // The marker is set only once the diagram has actually rendered. Set it
    // any earlier and an unrendered diagram is marked done, skipped on
    // every later scan, and never gets a button - which is what happens to
    // every diagram on a page reached by instant navigation, because the
    // new page's elements exist before Material has drawn them.
    if (!root.querySelector("svg")) return;
    host.dataset.dgz = "done";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "dgz__open";
    button.setAttribute("aria-label", "Open this diagram in the viewer");
    button.setAttribute("title", "Open the diagram in a full-screen viewer");
    button.textContent = "Expand";

    button.addEventListener("click", () => open(host));

    host.parentNode.insertBefore(button, host);

    // A diagram with a link in it keeps the link: clicking the diagram
    // would otherwise take the reader away from the page instead of
    // opening the viewer.
    const hasLink = Boolean(root.querySelector("a"));

    if (!hasLink) {
      host.addEventListener("click", (event) => {
        // A click that ended a text selection is a selection, not a
        // request to open the viewer.
        const selection = window.getSelection();
        if (selection && selection.toString().length > 0) return;
        open(host);
      });
    }
  }

  function scan() {
    document.querySelectorAll(".mermaid").forEach(decorate);
  }

  /**
   * Look for diagrams for a while, then stop.
   *
   * Material renders a Mermaid diagram asynchronously and replaces the
   * element it was given, so the shadow root appears some time after the
   * page's scripts have run. A single scan finds nothing; a scan repeated
   * forever is a timer nobody needs. The budget is a few seconds, which is
   * far longer than a render takes and still ends.
   *
   * It is *restarted* on every instant navigation rather than started once:
   * a one-shot budget spent on the first page leaves every later page's
   * diagrams undecorated, and the only symptom is a missing button.
   */
  let timer = null;

  function scheduleScan() {
    scan();
    if (timer !== null) clearInterval(timer);
    let attempts = 0;
    timer = setInterval(() => {
      scan();
      attempts += 1;
      if (attempts > 40) {
        clearInterval(timer);
        timer = null;
      }
    }, 250);
  }

  function init() {
    scheduleScan();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // MkDocs Material's instant navigation replaces the page content
  // without a full reload. The old diagrams go with it, so their marker is
  // cleared; the viewer is closed, because the page it described is gone;
  // and the scan is restarted for the new page's diagrams.
  document.addEventListener("DOMContentSwitch", () => {
    close();
    document.querySelectorAll("[data-dgz]").forEach((element) => {
      delete element.dataset.dgz;
    });
    document.querySelectorAll(".dgz__open").forEach((button) => {
      button.remove();
    });
    scheduleScan();
  });
})();
