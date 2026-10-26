// Pencil badges on the clickable parts of the diagram - on a touch screen
// there is no hover, so it must be visible what can be tapped.
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const TAPPABLE = ["#radiators-temp", "#auger"];
  const RADIUS = 12; // svg units
  const PENCIL =
    "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41" +
    "l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z";

  function addHint(svg, layer, target) {
    // top-right corner of the element, in the svg's own coordinates
    const box = target.getBoundingClientRect();
    const toSvg = svg.getScreenCTM().inverse();
    const corner = new DOMPoint(box.right, box.top).matrixTransform(toSvg);

    const hint = document.createElementNS(SVG_NS, "g");
    hint.setAttribute("class", "tap-hint");
    hint.setAttribute("transform", `translate(${corner.x - RADIUS * 0.4}, ${corner.y + RADIUS * 0.4})`);

    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("r", RADIUS);
    hint.appendChild(circle);

    const icon = document.createElementNS(SVG_NS, "path");
    icon.setAttribute("d", PENCIL);
    icon.setAttribute("transform", `translate(${-RADIUS * 0.6}, ${-RADIUS * 0.6}) scale(${RADIUS / 20})`);
    hint.appendChild(icon);

    hint.addEventListener("click", () => {
      target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    layer.appendChild(hint);
  }

  function init() {
    const svg = document.querySelector(".diagram svg");
    if (!svg || !svg.getScreenCTM()) return;

    const layer = document.createElementNS(SVG_NS, "g");
    layer.setAttribute("class", "tap-hints");
    svg.appendChild(layer);

    TAPPABLE.forEach((selector) => {
      const target = svg.querySelector(selector);
      if (target) addHint(svg, layer, target);
    });
  }

  window.addEventListener("load", init);
})();
