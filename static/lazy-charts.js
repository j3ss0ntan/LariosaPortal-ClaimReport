(function () {
  "use strict";

  function runScripts(container) {
    var scripts = container.querySelectorAll("script");
    scripts.forEach(function (oldScript) {
      var newScript = document.createElement("script");
      for (var i = 0; i < oldScript.attributes.length; i++) {
        var attr = oldScript.attributes[i];
        newScript.setAttribute(attr.name, attr.value);
      }
      newScript.textContent = oldScript.textContent;
      oldScript.replaceWith(newScript);
    });
  }

  function loadChart(el) {
    var tpl = el.querySelector("template");
    if (!tpl) return;
    var clone = tpl.content.cloneNode(true);
    el.innerHTML = "";
    el.appendChild(clone);
    runScripts(el);
    el.classList.add("loaded");
  }

  var targets = document.querySelectorAll(".lazy-chart");
  if (!targets.length) return;

  if (!("IntersectionObserver" in window)) {
    targets.forEach(loadChart);
    return;
  }

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          loadChart(entry.target);
          observer.unobserve(entry.target);
        }
      });
    },
    { rootMargin: "400px 0px", threshold: 0.01 }
  );

  targets.forEach(function (el) {
    observer.observe(el);
  });
})();
