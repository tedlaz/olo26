(function () {
  "use strict";

  function initializeWizard(root) {
    if (root.dataset.initialized) return;
    root.dataset.initialized = "true";

    const form = root.querySelector("[data-wizard-form]");
    const initialNode = root.parentElement.querySelector("[data-wizard-initial]");
    const initial = JSON.parse(initialNode.textContent || "null") || {};
    const state = {
      building: initial.building || { name: "", address: "", postal_code: "" },
      apartments: Array.isArray(initial.apartments) ? initial.apartments : [],
      categories: Array.isArray(initial.categories) ? initial.categories : [],
      millesimals: initial.millesimals || {},
    };
    let currentStep = Math.min(4, Math.max(1, Number(root.dataset.initialStep) || 1));
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

    function renderApartments() {
      const list = root.querySelector("[data-apartment-list]");
      list.innerHTML = state.apartments
        .map(
          (apartment, index) => `
            <article class="wizard-list-item" data-apartment-key="${escapeHtml(apartment.key)}">
              <header><strong>Διαμέρισμα ${index + 1}</strong><button type="button" class="link-button danger" data-remove-apartment>Αφαίρεση</button></header>
              <div class="apartment-wizard-fields">
                <label>Αριθμός<input type="number" step="1" data-apartment-field="number" value="${escapeHtml(apartment.number)}"></label>
                <label>Ονομασία<input maxlength="100" data-apartment-field="name" value="${escapeHtml(apartment.name)}"></label>
                <label>Όροφος<input type="number" step="1" data-apartment-field="floor" value="${escapeHtml(apartment.floor)}"></label>
                <label>Τ.μ.<input type="number" min="0" step="0.01" data-apartment-field="square_meters" value="${escapeHtml(apartment.square_meters)}"></label>
                <label>Ιδιοκτήτης<input maxlength="100" data-apartment-field="owner" value="${escapeHtml(apartment.owner)}"></label>
                <label>Ένοικος<input maxlength="100" data-apartment-field="occupant" value="${escapeHtml(apartment.occupant)}"></label>
              </div>
            </article>`,
        )
        .join("");
    }

    function renderCategories() {
      const list = root.querySelector("[data-category-list]");
      list.innerHTML = state.categories
        .map(
          (category, index) => `
            <article class="wizard-list-item category-item" data-category-key="${escapeHtml(category.key)}">
              <label>Τύπος δαπάνης ${index + 1}<input maxlength="100" data-category-field="name" value="${escapeHtml(category.name)}"></label>
              <button type="button" class="link-button danger" data-remove-category>Αφαίρεση</button>
            </article>`,
        )
        .join("");
    }

    function matrixValue(categoryKey, apartmentKey) {
      return state.millesimals[categoryKey]?.[apartmentKey] ?? 0;
    }

    function renderMatrix() {
      const container = root.querySelector("[data-matrix-container]");
      state.categories.forEach((category) => {
        state.millesimals[category.key] ||= {};
        state.apartments.forEach((apartment) => {
          state.millesimals[category.key][apartment.key] ??= 0;
        });
      });
      const headings = state.categories
        .map((category) => `<th scope="col">${escapeHtml(category.name)}</th>`)
        .join("");
      const rows = state.apartments
        .map((apartment) => {
          const cells = state.categories
            .map(
              (category) => `<td><input type="number" min="0" max="1000" step="1" aria-label="${escapeHtml(category.name)} - ${escapeHtml(apartment.name)}" data-matrix-category="${escapeHtml(category.key)}" data-matrix-apartment="${escapeHtml(apartment.key)}" value="${escapeHtml(matrixValue(category.key, apartment.key))}"></td>`,
            )
            .join("");
          return `<tr><th scope="row"><strong>${escapeHtml(apartment.number)}. ${escapeHtml(apartment.name)}</strong><small>${escapeHtml(apartment.floor)}ος όροφος</small></th>${cells}</tr>`;
        })
        .join("");
      const totals = state.categories
        .map((category) => `<td data-matrix-total="${escapeHtml(category.key)}">0</td>`)
        .join("");
      container.innerHTML = `<table class="matrix wizard-matrix"><thead><tr><th scope="col">Διαμέρισμα</th>${headings}</tr></thead><tbody>${rows}</tbody><tfoot><tr><th scope="row">Σύνολο</th>${totals}</tr></tfoot></table>`;
      updateTotals();
    }

    function updateTotals() {
      state.categories.forEach((category) => {
        const total = state.apartments.reduce(
          (sum, apartment) => sum + (Number(matrixValue(category.key, apartment.key)) || 0),
          0,
        );
        const cell = root.querySelector(`[data-matrix-total="${CSS.escape(category.key)}"]`);
        if (cell) {
          cell.textContent = total + " / 1000";
          cell.classList.toggle("is-valid", total === 1000);
          cell.classList.toggle("is-invalid", total !== 1000);
        }
      });
    }

    function setStep(step) {
      currentStep = step;
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
      root.querySelector("[data-wizard-next]").hidden = currentStep === 4;
      root.querySelector("[data-wizard-submit]").hidden = currentStep !== 4;
      showError("");
      root.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function validate(step) {
      if (step === 1) {
        if (!state.building.name.trim() || !state.building.address.trim() || !state.building.postal_code.trim()) {
          return "Συμπληρώστε όλα τα στοιχεία του κτιρίου.";
        }
      }
      if (step === 2) {
        if (!state.apartments.length) return "Προσθέστε τουλάχιστον ένα διαμέρισμα.";
        const numbers = new Set();
        for (const apartment of state.apartments) {
          if (!apartment.name.trim() || apartment.number === "" || !Number.isInteger(Number(apartment.number))) {
            return "Συμπληρώστε έγκυρο αριθμό και ονομασία για κάθε διαμέρισμα.";
          }
          if (!Number.isInteger(Number(apartment.floor)) || Number(apartment.square_meters) < 0 || apartment.square_meters === "") {
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
          const name = category.name.trim().toLocaleLowerCase("el");
          if (!name) return "Συμπληρώστε την ονομασία κάθε τύπου δαπάνης.";
          if (names.has(name)) return "Οι τύποι δαπανών πρέπει να έχουν μοναδικές ονομασίες.";
          names.add(name);
        }
      }
      if (step === 4) {
        for (const category of state.categories) {
          let total = 0;
          for (const apartment of state.apartments) {
            const value = Number(matrixValue(category.key, apartment.key));
            if (!Number.isInteger(value) || value < 0 || value > 1000) {
              return "Κάθε τιμή της μήτρας πρέπει να είναι ακέραιος από 0 έως 1000.";
            }
            total += value;
          }
          if (total !== 1000) return `Ο τύπος «${category.name}» έχει σύνολο ${total} αντί για 1000.`;
        }
      }
      return "";
    }

    root.querySelectorAll("[data-building-field]").forEach((input) => {
      input.value = state.building[input.dataset.buildingField] || "";
    });
    if (!state.apartments.length) {
      state.apartments.push({ key: makeKey("apartment"), number: "", name: "", floor: 0, square_meters: 0, owner: "", occupant: "" });
    }
    if (!state.categories.length) state.categories.push({ key: makeKey("category"), name: "" });
    renderApartments();
    renderCategories();
    renderMatrix();
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
        state.apartments.push({ key: makeKey("apartment"), number: "", name: "", floor: 0, square_meters: 0, owner: "", occupant: "" });
        renderApartments();
      } else if (button.matches("[data-remove-apartment]")) {
        const key = button.closest("[data-apartment-key]").dataset.apartmentKey;
        state.apartments = state.apartments.filter((item) => item.key !== key);
        Object.values(state.millesimals).forEach((column) => delete column[key]);
        renderApartments();
      } else if (button.matches("[data-add-category]")) {
        state.categories.push({ key: makeKey("category"), name: "" });
        renderCategories();
      } else if (button.matches("[data-remove-category]")) {
        const key = button.closest("[data-category-key]").dataset.categoryKey;
        state.categories = state.categories.filter((item) => item.key !== key);
        delete state.millesimals[key];
        renderCategories();
      } else if (button.matches("[data-wizard-next]")) {
        const error = validate(currentStep);
        if (error) return showError(error);
        if (currentStep === 3) renderMatrix();
        highestStep = Math.max(highestStep, currentStep + 1);
        setStep(currentStep + 1);
      } else if (button.matches("[data-wizard-previous]")) {
        setStep(currentStep - 1);
      } else if (button.matches("[data-step-target]")) {
        const targetStep = Number(button.dataset.stepTarget);
        if (targetStep <= highestStep && targetStep !== currentStep) setStep(targetStep);
      }
    });

    form.addEventListener("submit", function (event) {
      for (let step = 1; step <= 4; step += 1) {
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

  document.addEventListener("DOMContentLoaded", () => initializeAll(document));
  document.addEventListener("htmx:load", (event) => initializeAll(event.detail.elt));
})();
