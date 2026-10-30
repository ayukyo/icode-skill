/* No UI token, library access, navigation capability or host DOM access here. */
(() => {
  "use strict";
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", htmlLabels: false,
    flowchart: { htmlLabels: false }, suppressErrorRendering: true });
  let queue = Promise.resolve();
  const presentation = new Set(["fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity",
    "stroke-dasharray", "opacity", "font-size", "font-family", "font-weight", "font-style", "text-anchor"]);
  const withoutInlineStyles = (svg) => {
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    if (doc.querySelector("parsererror")) throw new Error("invalid SVG");
    const styles = [...doc.querySelectorAll('style')].map((style) => {
      const text = style.textContent;
      style.remove();
      return text;
    });
    doc.querySelectorAll("[style]").forEach((element) => {
      // SVG presentation attributes preserve diagram colors while the host
      // keeps style attributes disabled. URLs may reference local SVG IDs only.
      for (const property of [...element.style]) {
        const value = element.style.getPropertyValue(property);
        if (presentation.has(property) && !/[<>;{}]/.test(value)
            && (!/url\s*\(/i.test(value) || /^url\(["']?#[\w-]+["']?\)$/.test(value))) element.setAttribute(property, value);
      }
      element.removeAttribute("style");
    });
    return { svg: new XMLSerializer().serializeToString(doc.documentElement), styles };
  };
  addEventListener("message", (event) => {
    if (event.source !== parent || event.ports.length !== 1) return;
    const input = event.data;
    const port = event.ports[0];
    if (!input || input.type !== "icode:render-diagram" || !/^[a-zA-Z][\w-]{0,80}$/.test(input.id)
        || typeof input.source !== "string" || input.source.length > 65536
        || /(?:%%\{|^\s*---|\bclick\s|<\s*\/?\s*(?:script|iframe|img|a)\b)/im.test(input.source)) {
      port.postMessage({ id: input && input.id, ok: false });
      port.close();
      return;
    }
    queue = queue.then(async () => {
      try {
        const result = await mermaid.render(input.id, input.source);
        port.postMessage({ id: input.id, ok: true, ...withoutInlineStyles(result.svg) });
      } catch (_error) {
        port.postMessage({ id: input.id, ok: false });
      } finally {
        port.close();
      }
    });
  });
})();
