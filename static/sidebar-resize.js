(function () {
  "use strict";
  var STORAGE_KEY = "eurolink_sidebar_width";
  var MIN_WIDTH = 260;
  var MAX_WIDTH = 640;

  var sidebar = document.querySelector(".sidebar");
  var handle = document.querySelector(".sidebar-resize-handle");
  if (!sidebar || !handle) return;

  function clamp(px) {
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, px));
  }

  function applyWidth(px) {
    document.documentElement.style.setProperty("--sidebar-width", px + "px");
  }

  try {
    var saved = localStorage.getItem(STORAGE_KEY);
    if (saved) applyWidth(clamp(parseInt(saved, 10)));
  } catch (e) { /* localStorage unavailable (private mode, etc.) -- fall back to CSS default */ }

  var dragging = false;

  function start(e) {
    dragging = true;
    sidebar.classList.add("resizing");
    handle.classList.add("active");
    document.body.style.userSelect = "none";
    if (e.cancelable) e.preventDefault();
  }

  function move(clientX) {
    if (!dragging) return;
    applyWidth(clamp(clientX));
  }

  function end() {
    if (!dragging) return;
    dragging = false;
    sidebar.classList.remove("resizing");
    handle.classList.remove("active");
    document.body.style.userSelect = "";
    try {
      var current = getComputedStyle(document.documentElement)
        .getPropertyValue("--sidebar-width")
        .trim();
      if (current) localStorage.setItem(STORAGE_KEY, parseInt(current, 10));
    } catch (e) { /* ignore */ }
  }

  handle.addEventListener("mousedown", start);
  window.addEventListener("mousemove", function (e) { move(e.clientX); });
  window.addEventListener("mouseup", end);

  handle.addEventListener("touchstart", start, { passive: false });
  window.addEventListener("touchmove", function (e) {
    if (!dragging || !e.touches.length) return;
    move(e.touches[0].clientX);
  }, { passive: true });
  window.addEventListener("touchend", end);
})();
