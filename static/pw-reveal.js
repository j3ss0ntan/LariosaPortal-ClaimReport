(function () {
  "use strict";
  document.querySelectorAll(".pw-reveal").forEach(function (wrap) {
    var btn = wrap.querySelector(".pw-eye-btn");
    var text = wrap.querySelector(".pw-text");
    var openIcon = wrap.querySelector(".pw-eye-open");
    var closedIcon = wrap.querySelector(".pw-eye-closed");
    if (!btn || !text) return;

    var pw = wrap.getAttribute("data-pw") || "";
    var masked = "•".repeat(pw.length);
    var revealed = false;

    btn.addEventListener("click", function () {
      revealed = !revealed;
      text.textContent = revealed ? pw : masked;
      btn.setAttribute("aria-pressed", String(revealed));
      btn.setAttribute("aria-label", revealed ? "Hide password" : "Show password");
      if (openIcon && closedIcon) {
        openIcon.style.display = revealed ? "none" : "";
        closedIcon.style.display = revealed ? "" : "none";
      }
    });
  });
})();
