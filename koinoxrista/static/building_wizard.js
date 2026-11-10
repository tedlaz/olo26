(function () {
  "use strict";

  const initialized = new WeakSet();

  function text(value) {
    return value == null ? "" : String(value);
  }

  function initializeWizard(root) {
    if (initialized.has(root)) return;
    initialized.add(root);

    const form = root.querySelector("[data-wizard-form]");
    const initialNode = root.parentElement.querySelector("[data-wizard-initial]");
    const initial = JSON.parse(initialNode.textContent || "null") || {};
    const state = {
      building: initial.building || { name: "", address: "", postal_code: "" },
      apartments: Array.isArray(initial.apartments) ? initial.apartments : [],
      categories: Array.isArray(initial.categories) ? initial.categories : [],
      millesimals: initial.millesimals || {},
    };
    const lastStep = 4;
    let currentStep = Math.min(lastStep, Math.max(1, Number(root.dataset.initialStep) || 1));
    let highestStep = currentStep;
    let nextKey = Date.now();

    function makeKey(prefix) {
      nextKey += 1;
      return prefix + "-" + nextKey;
    }

    function escapeHtml(value) {
      const node = document.createElement("div");
      node.textContent = value == null ? "" : String(value);
      return node.innerHTML;
    }

    function showError(message) {
      const box = root.querySelector("[data-wizard-error]");
      box.textContent = message;
      box.hidden = !message;
      if (message) box.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    function newApartment() {
      const numbers = state.apartments.map((item) => Number(item.number)).filter(Number.isInteger);
      const number = numbers.length ? Math.max(...numbers) + 1 : 1;
      return { key: makeKey("apartment"), number: String(number), name: "", floor: 0, square_meters: 0, owner: "", occupant: "" };
    }

    function renderApartments() {
      const list = root.querySelector("[data-apartment-list]");
      const field = (apartment, name, label, attributes) =>
        `<input ${attributes} aria-label="${label}" data-apartment-field="${name}" value="${escapeHtml(apartment[name])}">`;
      const rows = state.apartments
        .map(
          (apartment) => `
            <tr data-apartment-key="${escapeHtml(apartment.key)}">
              <td class="narrow">${field(apartment, "number", "Αριθμός", 'type="number" step="1"')}</td>
              <td>${field(apartment, "name", "Ονομασία", 'maxlength="100"')}</td>
              <td class="narrow">${field(apartment, "floor", "Όροφος", 'type="number" step="1"')}</td>
              <td class="narrow">${field(apartment, "square_meters", "Τ.μ.", 'type="number" min="0" step="0.01"')}</td>
              <td>${field(apartment, "owner", "Ιδιοκτήτης", 'maxlength="100"')}</td>
              <td>${field(apartment, "occupant", "Ένοικος", 'maxlength="100"')}</td>
              <td class="row-action"><button type="button" class="row-remove" data-remove-apartment aria-label="Αφαίρεση διαμερίσματος" title="Αφαίρεση">&times;</button></td>
            </tr>`,
        )
        .join("");
      list.innerHTML = `<div class="table-wrap"><table class="wizard-grid">
        <thead><tr><th class="narrow">Αριθμός</th><th>Ονομασία</th><th class="narrow">Όροφος</th><th class="narrow">Τ.μ.</th><th>Ιδιοκτήτης</th><th>Ένοικος</th><th class="row-action"></th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
    }

    function ensureMillesimals() {
      state.categories.forEach((category) => {
        state.millesimals[category.key] ||= {};
        state.apartments.forEach((apartment) => {
          state.millesimals[category.key][apartment.key] ??= 0;
        });
      });
    }

    function matrixValue(categoryKey, apartmentKey) {
      return state.millesimals[categoryKey]?.[apartmentKey] ?? 0;
    }

    function renderCategories() {
      ensureMillesimals();
      const list = root.querySelector("[data-category-list]");
      const headings = state.categories
        .map(
          (category, index) => `
            <th data-category-key="${escapeHtml(category.key)}">
              <div class="category-head">
                <input maxlength="100" placeholder="π.χ. Θέρμανση" aria-label="Τύπος δαπάνης ${index + 1}" data-category-field="name" value="${escapeHtml(category.name)}">
                <button type="button" class="row-remove" data-remove-category aria-label="Αφαίρεση τύπου δαπάνης" title="Αφαίρεση">&times;</button>
              </div>
            </th>`,
        )
        .join("");
      const rows = state.apartments
        .map(
          (apartment) => `<tr><th scope="row">${escapeHtml(apartment.number)}. ${escapeHtml(apartment.name)}</th>${state.categories
            .map(
              (category) => `<td><input type="number" min="0" max="1000" step="1" inputmode="numeric" aria-label="${escapeHtml(category.name)} - ${escapeHtml(apartment.name)}" data-matrix-category="${escapeHtml(category.key)}" data-matrix-apartment="${escapeHtml(apartment.key)}" value="${escapeHtml(matrixValue(category.key, apartment.key))}"></td>`,
            )
            .join("")}</tr>`,
        )
        .join("");
      const totals = state.categories
        .map((category) => `<td class="matrix-total" data-matrix-total="${escapeHtml(category.key)}" aria-live="polite"><strong></strong><small></small></td>`)
        .join("");
      list.innerHTML = `<div class="table-wrap"><table class="wizard-grid category-matrix">
        <thead><tr><th>Διαμέρισμα</th>${headings}</tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><th>Σύνολο / υπόλοιπο</th>${totals}</tr></tfoot></table></div>`;
      updateTotals();
    }

    function updateTotals() {
      state.categories.forEach((category) => {
        const total = state.apartments.reduce(
          (sum, apartment) => sum + (Number(matrixValue(category.key, apartment.key)) || 0),
          0,
        );
        const cell = root.querySelector(`[data-matrix-total="${CSS.escape(category.key)}"]`);
        if (!cell) return;
        cell.querySelector("strong").textContent = `${total} / 1000`;
        cell.querySelector("small").textContent =
          total === 1000 ? "✓ σωστό" : total < 1000 ? `Υπόλοιπο ${1000 - total}` : `Υπέρβαση ${total - 1000}`;
        cell.classList.toggle("is-valid", total === 1000);
        cell.classList.toggle("is-invalid", total !== 1000);
      });
    }

    function renderSummary() {
      const container = root.querySelector("[data-summary]");
      const apartments = [...state.apartments].sort((a, b) => Number(a.number) - Number(b.number));
      const editButton = (step) =>
        `<button type="button" class="link-button" data-goto-step="${step}">Διόρθωση</button>`;
      const cell = (value) => escapeHtml(text(value).trim() || "—");
      const squareMeters = apartments.reduce((sum, item) => sum + (Number(item.square_meters) || 0), 0);

      const apartmentRows = apartments
        .map(
          (item) => `<tr><td class="numeric">${cell(item.number)}</td><td>${cell(item.name)}</td>
            <td class="numeric">${cell(item.floor)}</td><td class="numeric">${(Number(item.square_meters) || 0).toFixed(2)}</td>
            <td>${cell(item.owner)}</td><td>${cell(item.occupant)}</td></tr>`,
        )
        .join("");
      const headings = state.categories.map((c) => `<th class="numeric">${cell(c.name)}</th>`).join("");
      const matrixRows = apartments
        .map(
          (item) => `<tr><td>${cell(item.number)}. ${cell(item.name)}</td>${state.categories
            .map((c) => `<td class="numeric">${escapeHtml(text(matrixValue(c.key, item.key)))}</td>`)
            .join("")}</tr>`,
        )
        .join("");
      const totals = state.categories
        .map((c) => {
          const total = apartments.reduce((sum, item) => sum + (Number(matrixValue(c.key, item.key)) || 0), 0);
          return `<th class="numeric ${total === 1000 ? "is-valid" : "is-invalid"}">${total}</th>`;
        })
        .join("");

      container.innerHTML = `
        <section class="summary-section">
          <div class="summary-head"><h3>Κτίριο</h3>${editButton(1)}</div>
          <dl class="summary-building">
            <div><dt>Ονομασία</dt><dd>${cell(state.building.name)}</dd></div>
            <div><dt>Διεύθυνση</dt><dd>${cell(state.building.address)}</dd></div>
            <div><dt>ΤΚ</dt><dd>${cell(state.building.postal_code)}</dd></div>
          </dl>
        </section>
        <section class="summary-section">
          <div class="summary-head"><h3>Διαμερίσματα (${apartments.length})</h3>${editButton(2)}</div>
          <div class="table-wrap"><table>
            <thead><tr><th class="numeric">Αρ.</th><th>Ονομασία</th><th class="numeric">Όροφος</th><th class="numeric">m²</th><th>Ιδιοκτήτης</th><th>Ένοικος</th></tr></thead>
            <tbody>${apartmentRows}</tbody>
            <tfoot><tr><th colspan="3">Σύνολο</th><th class="numeric">${squareMeters.toFixed(2)}</th><th colspan="2"></th></tr></tfoot>
          </table></div>
        </section>
        <section class="summary-section">
          <div class="summary-head"><h3>Μήτρα χιλιοστών (${state.categories.length} ${state.categories.length === 1 ? "τύπος δαπάνης" : "τύποι δαπανών"})</h3>${editButton(3)}</div>
          <div class="table-wrap"><table class="summary-matrix">
            <thead><tr><th>Διαμέρισμα</th>${headings}</tr></thead>
            <tbody>${matrixRows}</tbody>
            <tfoot><tr><th>Σύνολο</th>${totals}</tr></tfoot>
          </table></div>
        </section>`;
    }

    function setStep(step) {
      currentStep = step;
      // apartments may have changed since the expense types were last shown
      if (currentStep === 3) renderCategories();
      if (currentStep === 4) renderSummary();
      root.querySelectorAll("[data-step]").forEach((panel) => {
        panel.hidden = Number(panel.dataset.step) !== currentStep;
      });
      root.querySelectorAll("[data-step-target]").forEach((button) => {
        const buttonStep = Number(button.dataset.stepTarget);
        button.disabled = buttonStep > highestStep;
        button.classList.toggle("active", buttonStep === currentStep);
        button.closest("li").classList.toggle("complete", buttonStep < highestStep);
        if (buttonStep === currentStep) button.setAttribute("aria-current", "step");
        else button.removeAttribute("aria-current");
      });
      root.querySelector("[data-wizard-previous]").hidden = currentStep === 1;
      root.querySelector("[data-wizard-next]").hidden = currentStep === lastStep;
      root.querySelector("[data-wizard-submit]").hidden = currentStep !== lastStep;
      showError("");
      root.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function validate(step) {
      if (step === 1) {
        const { name, address, postal_code: postalCode } = state.building;
        if (!text(name).trim() || !text(address).trim() || !text(postalCode).trim()) {
          return "Συμπληρώστε όλα τα στοιχεία του κτιρίου.";
        }
      }
      if (step === 2) {
        if (!state.apartments.length) return "Προσθέστε τουλάχιστον ένα διαμέρισμα.";
        const numbers = new Set();
        for (const apartment of state.apartments) {
          if (!text(apartment.name).trim() || text(apartment.number).trim() === "" || !Number.isInteger(Number(apartment.number))) {
            return "Συμπληρώστε έγκυρο αριθμό και ονομασία για κάθε διαμέρισμα.";
          }
          if (!Number.isInteger(Number(apartment.floor)) || Number(apartment.square_meters) < 0 || text(apartment.square_meters).trim() === "") {
            return "Ο όροφος πρέπει να είναι ακέραιος και τα τετραγωνικά μη αρνητικός αριθμός.";
          }
          if (numbers.has(String(Number(apartment.number)))) return "Οι αριθμοί των διαμερισμάτων πρέπει να είναι μοναδικοί.";
          numbers.add(String(Number(apartment.number)));
        }
      }
      if (step === 3) {
        if (!state.categories.length) return "Προσθέστε τουλάχιστον έναν τύπο δαπάνης.";
        const names = new Set();
        for (const category of state.categories) {
          const name = text(category.name).trim().toLocaleLowerCase("el");
          if (!name) return "Συμπληρώστε την ονομασία κάθε τύπου δαπάνης.";
          if (names.has(name)) return "Οι τύποι δαπανών πρέπει να έχουν μοναδικές ονομασίες.";
          names.add(name);
        }
        for (const category of state.categories) {
          let total = 0;
          for (const apartment of state.apartments) {
            const raw = text(matrixValue(category.key, apartment.key)).trim();
            const value = Number(raw);
            if (raw === "" || !Number.isInteger(value) || value < 0 || value > 1000) {
              return "Κάθε τιμή της μήτρας πρέπει να είναι ακέραιος από 0 έως 1000.";
            }
            total += value;
          }
          if (total !== 1000) return `Ο τύπος «${text(category.name).trim()}» έχει σύνολο ${total} χιλιοστά αντί για 1000.`;
        }
      }
      return "";
    }

    root.querySelectorAll("[data-building-field]").forEach((input) => {
      input.value = state.building[input.dataset.buildingField] || "";
    });
    if (!state.apartments.length) state.apartments.push(newApartment());
    if (!state.categories.length) state.categories.push({ key: makeKey("category"), name: "" });
    renderApartments();
    renderCategories();
    setStep(currentStep);

    root.addEventListener("input", function (event) {
      const input = event.target;
      if (input.dataset.buildingField) state.building[input.dataset.buildingField] = input.value;
      if (input.dataset.apartmentField) {
        const apartment = state.apartments.find((item) => item.key === input.closest("[data-apartment-key]").dataset.apartmentKey);
        apartment[input.dataset.apartmentField] = input.value;
      }
      if (input.dataset.categoryField) {
        const category = state.categories.find((item) => item.key === input.closest("[data-category-key]").dataset.categoryKey);
        category.name = input.value;
      }
      if (input.dataset.matrixCategory) {
        state.millesimals[input.dataset.matrixCategory] ||= {};
        state.millesimals[input.dataset.matrixCategory][input.dataset.matrixApartment] = input.value;
        updateTotals();
      }
    });

    root.addEventListener("click", function (event) {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.matches("[data-add-apartment]")) {
        state.apartments.push(newApartment());
        renderApartments();
        root.querySelector("[data-apartment-list] tbody tr:last-child [data-apartment-field=name]").focus();
      } else if (button.matches("[data-remove-apartment]")) {
        const key = button.closest("[data-apartment-key]").dataset.apartmentKey;
        state.apartments = state.apartments.filter((item) => item.key !== key);
        Object.values(state.millesimals).forEach((column) => delete column[key]);
        renderApartments();
      } else if (button.matches("[data-add-category]")) {
        state.categories.push({ key: makeKey("category"), name: "" });
        renderCategories();
        root.querySelector("[data-category-list] thead th:last-child [data-category-field]").focus();
      } else if (button.matches("[data-remove-category]")) {
        const key = button.closest("[data-category-key]").dataset.categoryKey;
        state.categories = state.categories.filter((item) => item.key !== key);
        delete state.millesimals[key];
        renderCategories();
      } else if (button.matches("[data-wizard-next]")) {
        const error = validate(currentStep);
        if (error) return showError(error);
        highestStep = Math.max(highestStep, currentStep + 1);
        setStep(currentStep + 1);
      } else if (button.matches("[data-wizard-previous]")) {
        setStep(currentStep - 1);
      } else if (button.matches("[data-step-target]")) {
        const targetStep = Number(button.dataset.stepTarget);
        if (targetStep <= highestStep && targetStep !== currentStep) goToStep(targetStep);
      } else if (button.matches("[data-goto-step]")) {
        goToStep(Number(button.dataset.gotoStep));
      }
    });

    function firstInvalidStep() {
      for (let step = 1; step < lastStep; step += 1) {
        const error = validate(step);
        if (error) return { step, error };
      }
      return null;
    }

    function goToStep(step) {
      const problem = step === lastStep ? firstInvalidStep() : null;
      if (problem) {
        setStep(problem.step);
        showError(problem.error);
        return;
      }
      setStep(step);
    }

    form.addEventListener("submit", function (event) {
      for (let step = 1; step <= lastStep; step += 1) {
        const error = validate(step);
        if (error) {
          event.preventDefault();
          setStep(step);
          showError(error);
          return;
        }
      }
      form.querySelector("[data-wizard-payload]").value = JSON.stringify(state);
      form.querySelector("[data-wizard-submit]").disabled = true;
    });

    root.querySelector("[data-wizard-cancel]").addEventListener("click", function (event) {
      if (!window.confirm("Να ακυρωθεί η δημιουργία; Κανένα στοιχείο δεν θα αποθηκευτεί.")) event.preventDefault();
    });
  }

  function initializeAll(container) {
    if (container.matches?.("[data-building-wizard]")) initializeWizard(container);
    container.querySelectorAll?.("[data-building-wizard]").forEach(initializeWizard);
  }

  // Loaded once from base.html: covers full page loads, hx-boost swaps and history restores.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initializeAll(document));
  } else {
    initializeAll(document);
  }
  document.addEventListener("htmx:load", (event) => initializeAll(event.detail.elt));
  document.addEventListener("htmx:historyRestore", () => initializeAll(document));
})();
