import { escapeHTML as e } from "./format.mjs";

export function calendarDays(month, available) {
  const [year, number] = month.split("-").map(Number),
    count = new Date(Date.UTC(year, number, 0)).getUTCDate();
  return Array.from({ length: count }, (_, index) => {
    const date = `${month}-${String(index + 1).padStart(2, "0")}`;
    return { date, day: index + 1, enabled: available.has(date) };
  });
}

// A native date input cannot disable holes within a dataset's date range.
export class DataCalendar {
  constructor(input, { optional = false } = {}) {
    this.input = input;
    this.optional = optional;
    this.dates = [];
    this.available = new Set();
    input.type = "text";
    input.readOnly = true;
    input.placeholder = optional ? "Same as first date" : "No dates loaded";
    input.setAttribute("aria-haspopup", "dialog");
    input.setAttribute("aria-label", optional ? "Last departure date" : "Departure date");
    input.setAttribute("aria-expanded", "false");
    this.panel = document.createElement("div");
    this.panel.id = `${input.name}-calendar`;
    this.panel.className = "data-calendar";
    this.panel.setAttribute("role", "dialog");
    this.panel.setAttribute("aria-label", optional ? "Last departure calendar" : "Departure calendar");
    this.panel.hidden = true;
    // Keep navigation controls mounted: replacing the clicked button makes the
    // document's outside-click handler treat month navigation as an outside click.
    this.panel.innerHTML = `<div class="calendar-heading"><button type="button" data-calendar-previous aria-label="Previous month with data">‹</button><button type="button" data-month-picker aria-label="Month with departure data" aria-expanded="false" aria-controls="${this.panel.id}-months"></button><button type="button" data-calendar-next aria-label="Next month with data">›</button></div>
      <div class="calendar-months" id="${this.panel.id}-months" role="group" aria-label="Available departure months" hidden></div>
      <div class="calendar-grid"></div><p class="calendar-summary small" aria-live="polite"></p>
      <div class="calendar-footer">${optional ? '<button type="button" data-date="">Use first date only</button>' : ""}<button type="button" data-close>Close</button></div>`;
    this.monthControl = this.panel.querySelector("[data-month-picker]");
    this.monthList = this.panel.querySelector(".calendar-months");
    this.monthControl.addEventListener("click", () => {
      this.showMonths(this.monthList.hidden);
      if (!this.monthList.hidden)
        this.monthList.querySelector('[aria-pressed="true"]')?.focus();
    });
    this.monthControl.addEventListener("keydown", (event) => {
      const index = this.months.indexOf(this.month),
        next = { ArrowUp: index - 1, ArrowDown: index + 1, Home: 0, End: this.months.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      this.month = this.months[Math.max(0, Math.min(this.months.length - 1, next))];
      this.render();
    });
    input.setAttribute("aria-controls", this.panel.id);
    input.parentElement.classList.add("calendar-field");
    input.after(this.panel);
    input.addEventListener("click", () => this.open());
    input.addEventListener("keydown", (event) => {
      if (["Enter", " ", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        this.open();
      }
    });
    this.panel.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      if (button.dataset.month) {
        this.month = button.dataset.month;
        this.monthControl.focus();
        this.showMonths(false);
        this.render();
      } else if (button.hasAttribute("data-date")) {
        input.value = button.dataset.date;
        this.close();
        input.focus();
        input.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (button.hasAttribute("data-close")) {
        this.close();
        input.focus();
      }
    });
    this.panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!this.monthList.hidden) {
          this.showMonths(false);
          this.monthControl.focus();
          return;
        }
        this.close();
        input.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (event.target !== input && !this.panel.contains(event.target)) this.close();
    });
    input.parentElement.addEventListener("focusout", (event) => {
      if (!input.parentElement.contains(event.relatedTarget)) this.close();
    });
  }
  setDates(dates, { preserveEmptySelection = false } = {}) {
    this.close();
    this.dates = [...new Set(dates)].sort();
    this.available = new Set(this.dates);
    this.months = [...new Set(this.dates.map((date) => date.slice(0, 7)))];
    this.input.disabled = !this.dates.length;
    if (!this.available.has(this.input.value) && !(preserveEmptySelection && !this.dates.length))
      this.input.value = this.optional ? "" : this.dates[0] || "";
  }
  open() {
    if (this.input.disabled || !this.dates.length) return;
    this.month = (this.input.value || this.dates[0]).slice(0, 7);
    this.render();
    this.panel.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
    (this.panel.querySelector('button[data-date][aria-pressed="true"]') ||
      this.panel.querySelector("button[data-date]:not(:disabled)"))?.focus();
  }
  close() {
    this.showMonths(false);
    this.panel.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
  }
  showMonths(show) {
    this.monthList.hidden = !show;
    this.monthControl.setAttribute("aria-expanded", String(show));
  }
  render() {
    const index = this.months.indexOf(this.month),
      previous = this.months[index - 1], next = this.months[index + 1],
      formatter = new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" }),
      offset = new Date(`${this.month}-01T00:00:00Z`).getUTCDay();
    for (const [selector, month] of [["[data-calendar-previous]", previous], ["[data-calendar-next]", next]]) {
      const button = this.panel.querySelector(selector);
      // Chromium blurs a focused button when it becomes disabled. Keep focus
      // inside the calendar before disabling a navigation boundary.
      if (!month && button === document.activeElement) this.monthControl.focus();
      button.disabled = !month;
      button.dataset.month = month || "";
    }
    this.monthControl.textContent = `${formatter.format(new Date(`${this.month}-01T00:00:00Z`))} ▾`;
    this.monthControl.dataset.value = this.month;
    if (this.monthList.children.length !== this.months.length ||
        [...this.monthList.children].some((button, i) => button.dataset.month !== this.months[i]))
      this.monthList.innerHTML = this.months.map((month) =>
        `<button type="button" data-month="${month}">${e(formatter.format(new Date(`${month}-01T00:00:00Z`)))}</button>`,
      ).join("");
    for (const button of this.monthList.children)
      button.setAttribute("aria-pressed", String(button.dataset.month === this.month));
    const days = calendarDays(this.month, this.available),
      count = days.filter((day) => day.enabled).length;
    const grid = this.panel.querySelector(".calendar-grid");
    if (grid.contains(document.activeElement)) this.monthControl.focus();
    grid.innerHTML = `${["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((day) => `<span>${day}</span>`).join("")}${"<span></span>".repeat(offset)}${days.map(({ date, day, enabled }) => `<button type="button" data-date="${date}" aria-label="${date}${enabled ? "" : ": no matching flight data"}" aria-pressed="${date === this.input.value}" ${enabled ? "" : "disabled"}>${day}</button>`).join("")}`;
    this.panel.querySelector(".calendar-summary").textContent =
      `${count} selectable departure date${count === 1 ? "" : "s"} this month. ${this.months.length === 1 ? "Only this month has selectable dates for this search." : "Months without selectable dates are omitted."}${this.optional ? " Last date must be within 30 days of the first." : ""}`;
  }
}
