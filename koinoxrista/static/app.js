(function () {
  "use strict";

  document.addEventListener(
    "submit",
    function (event) {
      const form = event.target.closest("form[data-confirm]");
      if (form && !window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  document.addEventListener("submit", async function (event) {
    const form = event.target.closest("form[data-backup-download]");
    if (!form || !window.fetch) return;

    event.preventDefault();
    const button = form.querySelector("button[type='submit'], button:not([type])");
    const idleText = button ? button.dataset.idleText || button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Δημιουργία backup...";
    }

    try {
      const prepareResponse = await fetch(form.dataset.prepareUrl, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const result = await prepareResponse.json();
      if (!prepareResponse.ok) throw new Error(result.error || "Η δημιουργία backup απέτυχε.");

      const historyResponse = await fetch(form.dataset.historyUrl, {
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
        cache: "no-store",
      });
      if (historyResponse.ok) {
        const target = document.querySelector("#backup-history-content");
        if (target) {
          target.innerHTML = await historyResponse.text();
          if (window.htmx) window.htmx.process(target);
        }
      }

      const download = document.createElement("a");
      download.href = result.download_url;
      download.download = result.filename;
      download.hidden = true;
      document.body.appendChild(download);
      download.click();
      download.remove();
    } catch (error) {
      window.alert(error.message || "Η δημιουργία backup απέτυχε.");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = idleText;
      }
    }
  });
})();
