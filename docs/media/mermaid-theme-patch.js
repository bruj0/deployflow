/*
 * Keep the Mermaid labels fixed, on the fixed light diagram card.
 *
 * Material injects its Mermaid `themeCSS` into the closed shadow root it
 * creates for each diagram, so a site stylesheet cannot reach those rules.
 * Without a correction the label colours there follow Material's own
 * `--md-mermaid-*` variables, so the text switches when the theme toggles.
 * `media/mermaid-card.css` keeps the surface light in both schemes, so the
 * correction is a single dark label colour for every label class.
 */
(function () {
  const initialize = mermaid.initialize.bind(mermaid);

  const fixes = [
    '.edgeLabel, .edgeLabel p, .label div .edgeLabel {',
    '  background-color: #ffffff !important;',
    '  color: #1c2d3e !important;',
    '  fill: #1c2d3e !important;',
    '}',
    '.labelText, .labelText > span {',
    '  fill: #1c2d3e !important;',
    '}',
    'text.noteText, text.noteText > tspan,',
    'text.sectionTitle, text.sectionTitle > tspan {',
    '  fill: #1c2d3e !important;',
    '}',
    'text.actor, text.actor-box, text.actor > tspan, text.actor-box > tspan {',
    '  fill: #1c2d3e !important;',
    '}',
    'text.messageText, text.sequenceNumber, text.loopText {',
    '  fill: #1c2d3e !important;',
    '}',
    '.nodeLabel, .nodeLabel p {',
    '  color: #1c2d3e !important;',
    '}',
  ].join('\n');

  mermaid.initialize = function (config) {
    return initialize({
      ...config,
      themeCSS: `${config?.themeCSS || ''}\n${fixes}`,
    });
  };
})();
