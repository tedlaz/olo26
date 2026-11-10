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

  // Greek date pickers: the field shows dd/mm/yyyy with Greek month/day names, while the
  // submitted value stays ISO (yyyy-mm-dd), so the server is unaffected.
  // Without flatpickr (CDN unreachable) the native date input still works.
  function greekDates(root) {
    if (!window.flatpickr || !root.querySelectorAll) return;
    root.querySelectorAll('input[type="date"]').forEach(function (input) {
      if (input._flatpickr) return;
      window.flatpickr(input, {
        locale: "gr",
        dateFormat: "Y-m-d",
        altInput: true,
        altFormat: "d/m/Y",
        allowInput: true,
        // inside a modal <dialog> the calendar must live in the dialog (top layer) to be clickable
        static: !!input.closest("dialog"),
        onOpen: function (_dates, _text, picker) {
          if (!picker.config.static) return; // flatpickr positions non-static calendars itself
          // open upwards when the window has more room above the field than below it
          const calendar = picker.calendarContainer;
          const field = picker.altInput.getBoundingClientRect();
          const below = window.innerHeight - field.bottom;
          calendar.classList.toggle(
            "open-above",
            below < calendar.offsetHeight + 8 && field.top > below,
          );
        },
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    greekDates(document);
  });
  document.addEventListener("htmx:load", function (event) {
    greekDates(event.detail.elt);
  });
  // htmx snapshots the page for Back/Forward: store plain inputs, re-enhance on restore.
  document.addEventListener("htmx:beforeHistorySave", function () {
    document.querySelectorAll(".flatpickr-input").forEach(function (input) {
      if (input._flatpickr) input._flatpickr.destroy();
    });
  });
  document.addEventListener("htmx:historyRestore", function () {
    greekDates(document);
  });

  // Millesimal matrix: live column total and remainder up to 1000 (first paint is server-rendered).
  document.addEventListener("input", function (event) {
    const input = event.target.closest && event.target.closest("input[data-matrix-col]");
    if (!input) return;
    const table = input.closest("table");
    const column = input.dataset.matrixCol;
    const cell = table.querySelector('[data-matrix-col-total="' + column + '"]');
    if (!cell) return;
    let total = 0;
    table.querySelectorAll('input[data-matrix-col="' + column + '"]').forEach(function (item) {
      total += Number(item.value) || 0;
    });
    cell.querySelector("strong").textContent = total;
    cell.querySelector("small").textContent =
      total === 1000 ? "✓ σωστό" : total < 1000 ? "Υπόλοιπο " + (1000 - total) : "Υπέρβαση " + (total - 1000);
    cell.classList.toggle("is-valid", total === 1000);
    cell.classList.toggle("is-invalid", total !== 1000);
  });

  // Modal forms.
  // [data-open="id"] opens an inline <dialog>; [data-close] closes its dialog.
  // a[data-modal] loads a form page into #modal (the page marks its form with [data-modal-content]).
  function showInModal(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const content = doc.querySelector("[data-modal-content]");
    const dialog = document.getElementById("modal");
    if (!content || !dialog) return false;
    dialog.querySelector(".modal-body").replaceChildren(...doc.querySelectorAll(".flash"), content);
    if (window.htmx) window.htmx.process(dialog);
    greekDates(dialog);
    if (!dialog.open) dialog.showModal();
    return true;
  }

  document.addEventListener(
    "click",
    async function (event) {
      const opener = event.target.closest("[data-open]");
      if (opener) {
        document.getElementById(opener.dataset.open)?.showModal();
        return;
      }
      if (event.target.closest("[data-close]")) {
        event.target.closest("dialog")?.close();
        return;
      }
      const link = event.target.closest("a[data-modal]");
      if (!link || !window.fetch) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      try {
        const response = await fetch(link.href, { credentials: "same-origin" });
        if (response.ok && showInModal(await response.text())) return;
      } catch (error) {
        // fall through to normal navigation
      }
      window.location.href = link.href;
    },
    true,
  );

  // A form inside #modal that comes back with validation errors stays in the modal;
  // a successful submit (redirect to another page) swaps the page as usual.
  document.addEventListener("htmx:beforeSwap", function (event) {
    // for boosted forms detail.elt is the swap target (<body>); the form is in requestConfig
    const source = (event.detail.requestConfig && event.detail.requestConfig.elt) || event.detail.elt;
    if (!source || !source.closest || !source.closest("#modal")) return;
    if (showInModal(event.detail.serverResponse)) event.detail.shouldSwap = false;
  });

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
