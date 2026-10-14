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
        this.render();
        this.panel.querySelector("button[data-date]:not(:disabled)")?.focus();
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
  setDates(dates) {
    this.close();
    this.dates = [...new Set(dates)].sort();
    this.available = new Set(this.dates);
    this.months = [...new Set(this.dates.map((date) => date.slice(0, 7)))];
    this.input.disabled = !this.dates.length;
    if (!this.available.has(this.input.value))
      this.input.value = this.optional ? "" : this.dates[0] || "";
  }
  open() {
    if (this.input.disabled || !this.dates.length) return;
    this.month = (this.input.value || this.dates[0]).slice(0, 7);
    this.render();
    this.panel.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
    (this.panel.querySelector('[aria-pressed="true"]') ||
      this.panel.querySelector("button[data-date]:not(:disabled)"))?.focus();
  }
  close() {
    this.panel.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
  }
  render() {
    const index = this.months.indexOf(this.month),
      previous = this.months[index - 1], next = this.months[index + 1],
      title = new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" })
        .format(new Date(`${this.month}-01T00:00:00Z`)),
      offset = new Date(`${this.month}-01T00:00:00Z`).getUTCDay();
    this.panel.innerHTML = `<div class="calendar-heading"><button type="button" aria-label="Previous month with data" ${previous ? `data-month="${previous}"` : "disabled"}>‹</button><strong aria-live="polite">${e(title)}</strong><button type="button" aria-label="Next month with data" ${next ? `data-month="${next}"` : "disabled"}>›</button></div>
      <div class="calendar-grid">${["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((day) => `<span>${day}</span>`).join("")}${"<span></span>".repeat(offset)}${calendarDays(this.month, this.available).map(({ date, day, enabled }) => `<button type="button" data-date="${date}" aria-label="${date}${enabled ? "" : ": no departure data"}" aria-pressed="${date === this.input.value}" ${enabled ? "" : "disabled"}>${day}</button>`).join("")}</div>
      <div class="calendar-footer">${this.optional ? '<button type="button" data-date="">Use first date only</button>' : ""}<button type="button" data-close>Close</button></div>`;
  }
}
