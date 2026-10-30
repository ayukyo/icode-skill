/* Mermaid's layout styles live in an opaque sandbox, outside the reader DOM. */
(() => {
  "use strict";
  let frameReady;
  const renderer = () => {
    if (!frameReady) frameReady = new Promise((resolve, reject) => {
      const frame = document.createElement("iframe");
      frame.className = "doc-diagram-frame";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("aria-hidden", "true");
      frame.title = "本地图表排版";
      frame.tabIndex = -1;
      const timer = setTimeout(() => { frameReady = null; frame.remove(); reject(new Error("图表引擎加载超时")); }, 15000);
      frame.addEventListener("load", () => { clearTimeout(timer); resolve(frame); }, { once: true });
      frame.addEventListener("error", () => { clearTimeout(timer); frameReady = null; frame.remove(); reject(new Error("图表引擎不可用")); }, { once: true });
      frame.src = "/assets/diagram-renderer.html";
      document.body.append(frame);
    });
    return frameReady;
  };
  globalThis.ICODEDiagramRenderer = Object.freeze({
    render: async (id, source) => {
      if (!/^[a-zA-Z][\w-]{0,80}$/.test(id) || typeof source !== "string" || source.length > 65536) throw new Error("图表输入超限");
      const frame = await renderer();
      return new Promise((resolve, reject) => {
        const channel = new MessageChannel();
        const finish = (error, value) => {
          clearTimeout(timer);
          channel.port1.close();
          error ? reject(error) : resolve(value);
        };
        const timer = setTimeout(() => finish(new Error("图表排版超时")), 30000);
        channel.port1.onmessage = (event) => {
          const result = event.data;
          if (!result || result.id !== id || !result.ok || typeof result.svg !== "string" || result.svg.length > 5 * 1024 * 1024
              || !Array.isArray(result.styles) || result.styles.length > 16 || result.styles.some((css) =>
                typeof css !== 'string' || css.length > 1024 * 1024 || /@(?:import|font-face)/i.test(css)
                || /url\(\s*(?!["']?#[\w-]+["']?\s*\))/i.test(css))) {
            finish(new Error("图表无法解析"));
          } else finish(null, { svg: result.svg, styles: result.styles });
        };
        // The sandbox has an opaque origin. A dedicated transferred port, not
        // a global message listener, binds this response to this exact request.
        frame.contentWindow.postMessage({ type: "icode:render-diagram", id, source }, "*", [channel.port2]);
      });
    },
  });
})();
