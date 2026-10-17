import { escapeHTML as e, RequestLane } from "./format.mjs";

// The committed value is an IATA code; free text searches airport metadata only.
export class AirportPicker {
  constructor(input, { airports, onLoad }) {
    this.input = input;
    this.airports = airports;
    this.onLoad = onLoad;
    this.list = input.parentElement.querySelector('[role="listbox"]');
    this.status = input.parentElement.querySelector('[role="status"]');
    this.clear = input.parentElement.querySelector(".airport-clear");
    this.lane = new RequestLane();
    this.options = [];
    this.active = -1;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", this.list.id);
    input.setAttribute("aria-expanded", "false");
    input.addEventListener("input", () => this.lookup());
    input.addEventListener("focus", () => {
      input.select();
      this.lookup();
    });
    input.addEventListener("click", () => {
      if (this.list.hidden) this.lookup();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" || event.key === "Tab") this.close();
      else if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        if (this.list.hidden) this.lookup();
        if (!this.options.length) return;
        const step = event.key === "ArrowDown" ? 1 : -1;
        this.active = this.active < 0
          ? (step === 1 ? 0 : this.options.length - 1)
          : (this.active + step + this.options.length) % this.options.length;
        this.render();
        this.list.children[this.active]?.scrollIntoView({ block: "nearest" });
      } else if (event.key === "Enter" && !this.list.hidden && this.options.length) {
        event.preventDefault();
        this.choose(this.options[Math.max(0, this.active)]);
      }
    });
    this.list.addEventListener("click", (event) => {
      const option = event.target.closest("[data-airport]");
      if (option)
        this.choose(this.options.find((a) => a.iata === option.dataset.airport));
    });
    this.clear.addEventListener("click", () => {
      input.value = "";
      input.focus();
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    input.parentElement.addEventListener("focusout", (event) => {
      if (event.relatedTarget && !input.parentElement.contains(event.relatedTarget))
        this.close();
    });
    document.addEventListener("pointerdown", (event) => {
      if (!input.parentElement.contains(event.target)) this.close();
    });
  }
  sync() {
    this.clear.hidden = !this.input.value;
  }
  close() {
    clearTimeout(this.timer);
    this.lane.cancel();
    this.list.hidden = this.status.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
    this.options = [];
    this.active = -1;
  }
  choose(airport) {
    if (!airport) return;
    this.input.value = airport.iata;
    this.input.focus();
    this.input.dispatchEvent(new Event("input", { bubbles: true }));
    this.close();
    this.sync();
  }
  lookup() {
    this.close();
    this.sync();
    const query = this.input.value.trim();
    if (query.length < 2) return;
    const match = query.toLowerCase();
    this.show(
      [...this.airports()].filter((a) =>
        [a.iata, a.name, a.city_id, a.municipality].some((s) =>
          s?.toLowerCase().includes(match),
        ),
      ),
      query,
    );
    this.timer = setTimeout(() => {
      this.status.hidden = false;
      this.status.textContent = "Searching airports…";
      this.lane.run(
        `/v1/airports?${new URLSearchParams({ q: query })}`,
        undefined,
        (airports) => {
          this.onLoad(airports);
          this.show(airports, query);
          this.status.textContent = this.options.length
            ? ""
            : "No matching airports. Try a name or three-letter code.";
          this.status.hidden = !!this.options.length;
        },
        () => {
          this.status.hidden = false;
          this.status.textContent =
            "Airport lookup unavailable. You can enter a three-letter code.";
        },
      );
    }, 200);
  }
  show(airports, query) {
    const activeCode = this.options[this.active]?.iata;
    this.options = airports
      .sort((a, b) =>
        Number(b.iata === query.toUpperCase()) -
          Number(a.iata === query.toUpperCase()) || a.iata.localeCompare(b.iata),
      )
      .slice(0, 12);
    this.active = this.options.findIndex((airport) => airport.iata === activeCode);
    this.render();
  }
  render() {
    this.list.innerHTML = this.options
      .map((a, index) =>
        `<button type="button" tabindex="-1" role="option" id="${this.list.id}-${index}" data-airport="${e(a.iata)}" aria-selected="${index === this.active}"><strong>${e(a.iata)}</strong><span>${e(a.name || a.iata)}${a.municipality ? `<small>${e(a.municipality)}</small>` : ""}</span></button>`,
      )
      .join("");
    this.list.hidden = !this.options.length;
    this.input.setAttribute("aria-expanded", String(!this.list.hidden));
    if (this.active < 0) this.input.removeAttribute("aria-activedescendant");
    else this.input.setAttribute("aria-activedescendant", `${this.list.id}-${this.active}`);
  }
}
