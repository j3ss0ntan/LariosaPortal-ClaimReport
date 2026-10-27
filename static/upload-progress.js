(function () {
  "use strict";
  var form = document.getElementById("upload-form");
  var overlay = document.getElementById("upload-progress-overlay");
  var circle = document.getElementById("upload-progress-circle");
  var percentText = document.getElementById("upload-progress-percent");
  var label = document.getElementById("upload-progress-label");
  if (!form || !overlay || !circle || !percentText || !label) return;

  var RADIUS = circle.r.baseVal.value;
  var CIRCUMFERENCE = 2 * Math.PI * RADIUS;
  circle.style.strokeDasharray = CIRCUMFERENCE + " " + CIRCUMFERENCE;

  function setProgress(pct) {
    circle.style.strokeDasharray = CIRCUMFERENCE + " " + CIRCUMFERENCE;
    circle.style.strokeDashoffset = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE;
    percentText.textContent = Math.round(pct) + "%";
  }

  function setSpinning() {
    // a full circle has no visible motion when rotated -- shrink to a partial
    // arc so the spin animation actually reads as spinning, not frozen.
    var arc = CIRCUMFERENCE * 0.25;
    circle.style.strokeDasharray = arc + " " + CIRCUMFERENCE;
    circle.style.strokeDashoffset = 0;
  }

  form.addEventListener("submit", function (e) {
    var fileInput = form.querySelector('input[type="file"]');
    if (!fileInput || !fileInput.files || !fileInput.files.length) return; // nothing chosen -- let it submit normally

    e.preventDefault();
    setProgress(0);
    label.textContent = "Uploading…";
    overlay.classList.remove("indeterminate");
    overlay.classList.add("visible");

    var xhr = new XMLHttpRequest();
    xhr.open("POST", form.action, true);

    xhr.upload.addEventListener("progress", function (ev) {
      if (ev.lengthComputable) setProgress((ev.loaded / ev.total) * 100);
    });
    xhr.upload.addEventListener("load", function () {
      label.textContent = "Processing…";
      overlay.classList.add("indeterminate");
      setSpinning();
    });
    xhr.onload = function () {
      var target = xhr.responseURL || form.action;
      if (target === window.location.href) {
        window.location.reload();
      } else {
        window.location.href = target;
      }
    };
    xhr.onerror = function () {
      overlay.classList.remove("visible");
      window.location.reload();
    };

    xhr.send(new FormData(form));
  });
})();
