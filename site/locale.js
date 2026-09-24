/* Choose a public page language locally; keep manual choices across visits. */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var root = new URL(".", script.src);
  var path = window.location.pathname;
  if (path.indexOf(root.pathname) !== 0) return;

  var relative = path.slice(root.pathname.length);
  var current = relative.indexOf("en/") === 0 ? "en" : "zh-CN";
  var params = new URLSearchParams(window.location.search);
  var manual = params.get("lang");
  var key = "icode.site.locale";
  var preferred = null;

  if (manual === "en" || manual === "zh-CN") {
    preferred = manual;
    try { window.localStorage.setItem(key, manual); } catch (_) { /* Storage may be disabled. */ }
    params.delete("lang");
    var query = params.toString();
    window.history.replaceState(null, "", path + (query ? "?" + query : "") + window.location.hash);
  } else {
    // A localized URL is an explicit choice; detect only at the default home entry.
    if (relative !== "" && relative !== "index.html") return;
    try { preferred = window.localStorage.getItem(key); } catch (_) { /* Use browser language. */ }
    if (preferred !== "en" && preferred !== "zh-CN") {
      preferred = null;
      var languages = (navigator.languages || []).slice();
      languages.push(navigator.language);
      for (var i = 0; i < languages.length; i += 1) {
        var language = String(languages[i] || "").toLowerCase();
        if (language.indexOf("zh") === 0) { preferred = "zh-CN"; break; }
        if (language.indexOf("en") === 0) { preferred = "en"; break; }
      }
      if (!preferred) preferred = "zh-CN";
    }
  }

  if (preferred === current) return;
  var target = preferred === "en" ? "en/" + relative : relative.slice(3);
  window.location.replace(new URL(target + window.location.search + window.location.hash, root).href);
}());
