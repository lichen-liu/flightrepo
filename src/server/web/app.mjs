import {
  escapeHTML as e,
  departureTime,
  arrivalTime,
  flightLabel,
  flightTimeLabel,
  ref,
  duration,
  stamp,
  localDate,
  localInput,
  addDate,
  errorMessage,
  queryLink,
  RequestLane,
  airportList,
  airportListSuggestions,
} from "./format.mjs";
import { RouteMap, drawTimeline } from "./map.mjs";
import { RequestActivity } from "./activity.mjs";
import {
  routeChain,
  durationChart,
  journeyAirports,
} from "./journey-visuals.mjs";
import { AirportPicker } from "./airport-picker.mjs";
import { DataCalendar } from "./calendar.mjs";
import { qualityBadges, qualityDetails } from "./quality.mjs";

const $ = (id) => document.getElementById(id);
const form = $("search-form"),
  editorForm = $("connection-form");
const field = (name) => form.elements.namedItem(name),
  editField = (name) => editorForm.elements.namedItem(name);
const activity = new RequestActivity($("request-progress"));
const track = (label, priority) => () => activity.begin(label, priority);
const searchLane = new RequestLane(track("Finding flight routes", 5)),
  catalogLane = new RequestLane(),
  bootstrapLane = new RequestLane(track("Loading airports", 1)),
  datesLane = new RequestLane(track("Checking available dates", 3)),
  editLane = new RequestLane(track("Updating your connection", 5));
const firstCalendar = new DataCalendar(field("date")),
  lastCalendar = new DataCalendar(field("last-date"), { optional: true });
let datesKey = null,
  availableDates = new Set(),
  userEdited = false,
  utcReviewPending = false,
  timeBasisEdited = false;
let availability = null;
const map = new RouteMap($("route-map"), $("map-info"));
const state = {
  catalog: null,
  airports: new Map(),
  result: null,
  searchRequest: null,
  selection: null,
  history: [],
  editing: null,
  preview: null,
  cursor: null,
  queryValues: null,
};
const airportPickers = ["origin", "destination"].map((name) =>
  new AirportPicker(field(name), {
    airports: () => state.airports.values(),
    onLoad: (airports) => {
      for (const airport of airports) state.airports.set(airport.iata, airport);
      updateZones();
    },
  }),
);
function metadata(code, source = state.selection) {
  // Missing snapshot facts stay unknown; an older catalog cannot fill them in.
  if (Array.isArray(source?.airports))
    return source.airports.find((a) => a.iata === code);
  return state.airports.get(code);
}
const zone = (code, source = state.selection) =>
  $("display-clock").value === "UTC"
    ? "UTC"
    : metadata(code, source)?.timezone || "UTC";
const inputZone = (code, source = null) =>
  field("input-clock").value === "UTC"
    ? "UTC"
    : (source ? metadata(code, source) : state.airports.get(code))?.timezone ||
      "UTC";
const label = (code) => metadata(code)?.name || code;
const showError = (id, text) => {
  $(id).textContent = text;
  $(id).hidden = !text;
};
function acceptTimeError(error) {
  const detail = error?.detail;
  if (detail?.code === "airport_timezone_changed") {
    const { airport, actual_timezone } = detail.context || {},
      previous = state.airports.get(airport);
    if (previous)
      state.airports.set(airport, { ...previous, timezone: actual_timezone });
    updateZones();
    updateEditorZone();
  }
}

const airportCode = (value) => value.trim().toUpperCase();
const timeBasisLabel = (basis) =>
  ({
    scheduled: "Published schedules",
    observed: "Observed historical legs",
    actual: "Reported actual times",
  })[basis] || basis;
const dateKey = () =>
  [
    airportCode(field("origin").value),
    airportCode(field("destination").value),
    field("input-clock").value,
    field("time-basis").value,
    field("date").value,
    field("last-date").value,
    field("onward-days").value,
  ].join("/");
function updateDatasetTag() {
  const data = state.catalog?.dataset;
  if (!data) return;
  const basis = field("time-basis").value,
    count = data.time_bases?.[basis] ?? data.flight_count,
    demo = data.data_mode === "synthetic";
  $("dataset-tag").textContent =
    `${demo ? "Demo · " : ""}${timeBasisLabel(basis)} · ${count} searchable legs across all origins${data.needs_review_count ? ` · ${data.needs_review_count} recommended for review` : ""}`;
}
function updateSearchButton() {
  $("search-button").disabled =
    !!searchLane.controller ||
    datesKey !== dateKey() ||
    !availability?.outbound ||
    !availability?.inbound ||
    !availableDates.has(field("date").value) ||
    !availableDates.has(field("last-date").value || field("date").value);
}
function updateWorkspaceState(empty) {
  document.querySelector(".workspace").classList.toggle("empty-results", empty);
  for (const button of document.querySelectorAll("[data-view]"))
    if (button.tagName === "BUTTON")
      button.disabled = empty && button.dataset.view !== "options";
  if (empty) setView("options");
}
function normalizeAirportInput(control) {
  if (control.hasAttribute("data-airport-search")) return;
  const list = control.hasAttribute("data-airport-list"),
    value = list ? control.value.toUpperCase() : airportCode(control.value);
  if (value === control.value) return;
  const leading = list
      ? 0
      : control.value.length - control.value.trimStart().length,
    start = control.selectionStart,
    end = control.selectionEnd;
  control.value = value;
  if (document.activeElement === control && start !== null)
    control.setSelectionRange(
      Math.max(0, start - leading),
      Math.max(0, end - leading),
    );
}
function updateAirportNames() {
  airportPickers.forEach((picker) => picker.sync());
  for (const [control, id, optional] of [
    [field("origin"), "origin-name", false],
    [field("destination"), "destination-name", false],
    [editField("next-airport"), "next-airport-name", true],
  ]) {
    const code = airportCode(control.value),
      name = state.airports.get(code)?.name;
    $(id).hidden = optional && !code;
    $(id).textContent =
      name ||
      (/^[A-Z]{3}$/.test(code)
        ? "Airport details not loaded"
        : optional ? "Three-letter airport code" : "Search by city, name or code");
  }
  const codes = field("via")
    .value.toUpperCase()
    .split(/[\s,]+/)
    .filter(Boolean);
  $("via-name").hidden = !codes.length;
  $("via-name").textContent = codes
    .map(
      (code) =>
        `${code}: ${state.airports.get(code)?.name || "Airport details not loaded"}`,
    )
    .join(" → ");
  updateViaSuggestions();
}
const viaLookupLane = new RequestLane();
let viaLookupTimer;
let viaOptions = [],
  viaActive = -1;
function hideViaSuggestions() {
  $("via-suggestions").hidden = true;
  field("via").setAttribute("aria-expanded", "false");
  field("via").removeAttribute("aria-activedescendant");
  viaActive = -1;
}
function updateViaSuggestions(open = false) {
  const control = field("via");
  viaOptions = airportListSuggestions(
    control.value,
    control.selectionStart ?? control.value.length,
    [...state.airports.keys()].filter(
      (code) =>
        ![
          airportCode(field("origin").value),
          airportCode(field("destination").value),
        ].includes(code),
    ),
  ).slice(0, 8);
  hideViaSuggestions();
  $("via-suggestions").innerHTML = viaOptions
    .map(
      ({ code, value }, index) =>
        `<span role="option" id="via-option-${index}" data-via-option="${index}" data-value="${e(value)}" aria-selected="false"><strong>${e(code)}</strong><span>${e(state.airports.get(code).name || code)}</span></span>`,
    )
    .join("");
  if (open && document.activeElement === control && viaOptions.length) {
    $("via-suggestions").hidden = false;
    control.setAttribute("aria-expanded", "true");
  }
}
function chooseVia(index) {
  const option = viaOptions[index];
  if (!option) return;
  const control = field("via");
  control.value = option.value;
  control.setSelectionRange(option.caret, option.caret);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  hideViaSuggestions();
}
function values() {
  return Object.fromEntries(
    [
      "origin",
      "destination",
      "via",
      "date",
      "last-date",
      "onward-days",
      "stops",
      "ranking",
      "minimum",
      "maximum",
      "avoid",
      "input-clock",
      "time-basis",
    ]
      .map((name) => [
        name,
        field(name).hasAttribute("data-airport-list")
          ? field(name)
              .value.toUpperCase()
              .split(/[\s,]+/)
              .filter(Boolean)
              .join(", ")
          : field(name).hasAttribute("data-airport-code")
            ? airportCode(field(name).value)
            : field(name).value,
      ])
      .concat([
        ["airport-change", field("airport-change").checked ? "1" : "0"],
      ]),
  );
}
function restore(values) {
  for (const [name, value] of Object.entries(values)) {
    const control = field(name);
    if (!control) continue;
    if (
      name === "stops" &&
      /^\d+$/.test(value) &&
      ![...control.options].some((option) => option.value === value)
    )
      control.add(new Option(`${value} stops`, value));
    if (control.type === "checkbox") control.checked = value === "1";
    else control.value = value;
    if (control.hasAttribute("data-airport-code"))
      normalizeAirportInput(control);
  }
}
function updateZones() {
  updateAirportNames();
  const origin = airportCode(field("origin").value),
    local = field("input-clock").value === "local",
    clock = (code) =>
      local
        ? state.airports.get(code)?.timezone || "timezone not loaded"
        : "UTC";
  $("date-zones").textContent = !origin
    ? "Dates use the departure airport’s local time. Choose UTC below to change this."
    : `Departure dates: ${clock(origin)} · ${field("onward-days").value === "" ? "Arrivals use all available data." : `Arrivals searched up to ${field("onward-days").value} days after the departure window.`}`;
  const missing =
    /^[A-Z]{3}$/.test(origin) && !state.airports.get(origin)?.timezone;
  if (missing && local)
    $("date-zones").textContent +=
      ` · timezone details not loaded for ${origin}; choose UTC or load airport data`;
}
function updateLastDates() {
  const first = field("date").value;
  lastCalendar.setDates(
    [...availableDates].filter(
      (date) => date >= first,
    ),
  );
}
async function loadDates() {
  const origin = airportCode(field("origin").value),
    destination = airportCode(field("destination").value),
    clock = field("input-clock").value,
    basis = field("time-basis").value,
    key = dateKey(),
    requestedDate = field("date").value,
    focusedDate = [field("date"), field("last-date")].includes(
      document.activeElement,
    )
      ? document.activeElement
      : null;
  if (key === datesKey) return;
  datesKey = key;
  datesLane.cancel();
  availability = null;
  availableDates = new Set();
  $("date-recovery").hidden = true;
  $("date-clock-notice").hidden = true;
  $("retry-dates").hidden = true;
  $("coverage-warning").hidden = true;
  $("coverage-scope").hidden = false;
  $("coverage-scope").textContent =
    "Checking departures on the selected date and destination arrivals within the search horizon…";
  for (const name of ["origin", "destination"]) {
    $(`${name}-coverage`).hidden = false;
    $(`${name}-coverage`).textContent = "Checking flights…";
  }
  firstCalendar.close();
  lastCalendar.close();
  field("date").disabled = field("last-date").disabled = true;
  field("date").placeholder = "Choose airports first";
  $("search-button").disabled = true;
  if (![origin, destination].every((code) => /^[A-Z]{3}$/.test(code))) {
    $("date-availability").textContent =
      "Choose departure and arrival airports to see available dates.";
    $("coverage-scope").hidden = true;
    for (const name of ["origin", "destination"])
      $(`${name}-coverage`).hidden = true;
    return;
  }
  $("date-availability").textContent = "Loading departure dates…";
  const params = new URLSearchParams({
    origin,
    destination,
    clock,
    time_basis: basis,
  });
  if (field("onward-days").value !== "") params.set("onward_days", field("onward-days").value);
  if (requestedDate) params.set("departure_date", requestedDate);
  if (field("last-date").value)
    params.set("departure_last_date", field("last-date").value);
  await datesLane.run(
    `/v1/search-availability?${params}`,
    undefined,
    (result) => {
      availability = result;
      availableDates = new Set(result.dates);
      if (result.selected_date) field("date").value = result.selected_date;
      firstCalendar.setDates(result.dates, { preserveEmptySelection: true });
      updateLastDates();
      datesKey = dateKey();
      if (clock === "local") {
        state.airports.set(origin, {
          ...state.airports.get(origin),
          iata: origin,
          timezone: result.timezone,
        });
        updateZones();
      }
      $("origin-coverage").textContent =
        `Outbound: ${result.outbound.toLocaleString()} on ${result.selected_date || "the departure date"}`;
      $("destination-coverage").textContent =
        `Inbound: ${result.inbound.toLocaleString()} within the arrival window`;
      $("coverage-scope").textContent =
        `Searchable ${timeBasisLabel(basis).toLowerCase()} · exact airports · ${result.timezone}. Outbound uses the first departure date; inbound uses that date through ${field("onward-days").value === "" ? "the latest available arrival" : field("onward-days").value + " days after the departure window"}. Counts do not guarantee a connecting route.`;
      const warnings = [];
      if (!result.outbound)
        warnings.push(
          `No searchable departures from ${origin} on ${result.selected_date || "the departure date"}.`,
        );
      if (!result.inbound)
        warnings.push(
          `No searchable arrivals into ${destination} in this search window.`,
        );
      $("coverage-warning").textContent = warnings.join(" ");
      $("coverage-warning").hidden = !warnings.length;
      if (!result.outbound || !result.inbound)
        field("date").disabled = field("last-date").disabled = true;
      field("date").placeholder = result.dates.length ? "Choose a date" : "No available dates";
      // Disabling an input during refresh removes focus in Chromium. Restore it
      // only if the traveler has not moved to another control while waiting.
      if (
        focusedDate &&
        !focusedDate.disabled &&
        document.activeElement === document.body
      )
        focusedDate.focus();
      $("date-availability").textContent = result.dates.length
        ? `${result.dates.length} departure dates with data from ${origin} · ${timeBasisLabel(basis)} · ${result.timezone} · ${result.dates[0]} to ${result.dates.at(-1)}. Dates require departures from ${origin} and arrivals into ${destination} within the search horizon; no direct flight is required.`
        : `No selectable departure dates for ${origin} → ${destination}. Dates need outbound flights and later destination arrivals within the search horizon.`;
      if (
        requestedDate &&
        result.dates.length &&
        field("date").value !== requestedDate
      )
        $("date-availability").textContent +=
          ` No matching departure data on ${requestedDate}; selected ${field("date").value}.`;
      if (!state.result && !searchLane.controller) {
        updateWorkspaceState(!result.dates.length);
        $("options").innerHTML = result.dates.length
          ? '<p class="empty">Choose departure dates and search to find a route.</p>'
          : `<p class="empty">${e($("coverage-warning").textContent || $("date-availability").textContent)}</p>`;
        $("applied-query").textContent =
          `${origin} → ${destination}${requestedDate ? ` · ${requestedDate}` : ""}`;
        $("status").textContent = result.dates.length
          ? "Departure data loaded. Search to find a route."
          : "No eligible departure dates for this search.";
      }
      updateSearchButton();
      if (utcReviewPending && clock === "UTC") {
        $("date-clock-notice").textContent = result.dates.length
          ? "Dates now use UTC. Review the departure date before searching."
          : "Dates now use UTC. No departure dates are available for this search.";
        $("date-clock-notice").hidden = false;
      }
    },
    (error) => {
      datesKey = null;
      $("date-availability").textContent = errorMessage(error);
      document.querySelector(".coverage-details").open = true;
      for (const name of ["origin", "destination"])
        $(`${name}-coverage`).textContent = "Flight counts unavailable.";
      $("retry-dates").hidden = false;
      if (clock === "local" && error?.detail?.code === "airport_metadata_missing") {
        $("date-recovery-message").textContent =
          "Local dates are unavailable for this airport. Use UTC dates to continue.";
        $("date-recovery").hidden = false;
        $("status").textContent = "Choose UTC dates to continue, or retry after airport details are updated.";
      }
    },
  );
}
$("retry-dates").addEventListener("click", loadDates);
$("use-utc-dates").addEventListener("click", async () => {
  userEdited = true;
  utcReviewPending = true;
  field("input-clock").value = "UTC";
  // A local calendar date is not a UTC date. Get a fresh selection explicitly.
  field("date").value = field("last-date").value = "";
  datesKey = null;
  updateZones();
  closeEditor();
  await loadDates();
  if (field("input-clock").value === "UTC" && availability && datesKey === dateKey()) {
    if (!field("date").disabled) field("date").focus();
  }
});
function commonQuery() {
  const via = airportList(field("via").value);
  return {
    origin: airportCode(field("origin").value),
    destination: airportCode(field("destination").value),
    via_airport: via.length ? via : null,
    max_legs:
      field("stops").value === "any" ? null : Number(field("stops").value) + 1,
    ranking: field("ranking").value,
    minimum_connection_minutes: Number(field("minimum").value),
    maximum_connection_minutes:
      field("maximum").value === "any" ? null : Number(field("maximum").value),
    allow_airport_change: field("airport-change").checked,
    avoid_airports: field("avoid")
      .value.split(/[\s,]+/)
      .filter(Boolean)
      .map((v) => v.toUpperCase()),
    trace: false,
    time_basis: field("time-basis").value,
  };
}
function searchRequest({ checkAvailability = true } = {}) {
  const query = commonQuery(),
    first = field("date").value,
    last = field("last-date").value || first;
  if (!first) throw Error("Choose a departure date.");
  if (
    checkAvailability &&
    (datesKey !== dateKey() ||
      !availableDates.has(first) ||
      !availableDates.has(last))
  )
    throw Error(
      "Choose dates with origin departures and destination arrivals within the search horizon.",
    );
  if (last < first)
    throw Error("Last departure date must be on or after the first.");
  if (query.origin === query.destination)
    throw Error("Choose different origin and destination airports.");
  const capacity =
    query.max_legs === null
      ? Infinity
      : (query.max_legs - 1) * (query.allow_airport_change ? 2 : 1);
  if ((query.via_airport?.length || 0) > capacity)
    throw Error(
      "Increase Maximum stops or choose Any number under Search filters to visit all via airports.",
    );
  for (const code of query.via_airport || []) {
    if ([query.origin, query.destination].includes(code))
      throw Error(
        `${code} is an endpoint. Via airports must be intermediate airports.`,
      );
    if (query.avoid_airports.includes(code))
      throw Error(
        `${code} is both a via and an avoided airport. Remove one constraint.`,
      );
  }
  if (field("input-clock").value === "local")
    return {
      url: "/v1/itineraries/search/local",
      body: {
        ...query,
        departure_date: first,
        departure_last_date: last,
        onward_days: field("onward-days").value === "" ? null : Number(field("onward-days").value),
        departure_timezone: state.airports.get(query.origin)?.timezone || null,
      },
    };
  return {
    url: "/v1/itineraries/search",
    body: {
      ...query,
      departure_start: `${first}T00:00:00Z`,
      departure_end: `${addDate(last, 1)}T00:00:00Z`,
      ...(field("onward-days").value === "" ? {} : {horizon_end: `${addDate(last, Number(field("onward-days").value) + 1)}T00:00:00Z`}),
    },
  };
}
async function search(options) {
  let request;
  try {
    request = searchRequest(options);
  } catch (error) {
    showError("error", errorMessage(error));
    return;
  }
  const submitted = values();
  field("via").value = submitted.via;
  updateAirportNames();
  closeEditor();
  showError("error", "");
  $("status").textContent = "Searching flights…";
  $("results").setAttribute("aria-busy", "true");
  $("map-loading").hidden = false;
  document.querySelector(".map-panel").setAttribute("aria-busy", "true");
  $("search-button").disabled = true;
  await searchLane.run(
    request.url,
    request.body,
    (result) => {
      state.result = result;
      state.searchRequest = request;
      state.history = [];
      state.queryValues = submitted;
      for (const airport of result.airports)
        state.airports.set(airport.iata, airport);
      updateZones();
      state.selection = result.items[0]
        ? {
            ...result.items[0],
            airports: result.airports,
            diagnostics: result.diagnostics,
            complete: true,
            edited: false,
          }
        : null;
      $("status").textContent =
        `${result.items.length} journey${result.items.length === 1 ? "" : "s"} found.${JSON.stringify(values()) !== JSON.stringify(submitted) ? " Inputs changed during the search; search again to apply them." : ""}`;
      const url = new URL(queryLink(submitted));
      history.replaceState(null, "", url.pathname + url.search);
      renderOptions();
      renderJourney();
      renderDatasetInfo();
      setView("options");
    },
    (error) => {
      acceptTimeError(error);
      showError("error", errorMessage(error));
      $("status").textContent = state.result
        ? "Search failed. Previous results remain visible."
        : "Search failed. Adjust the input or retry.";
    },
    () => {
      updateSearchButton();
      $("results").setAttribute("aria-busy", "false");
      $("map-loading").hidden = true;
      document.querySelector(".map-panel").setAttribute("aria-busy", "false");
    },
  );
}
async function moreRoutes() {
  const previous = state.result,
    request = state.searchRequest;
  if (!previous || previous.next_offset == null || !request) return;
  $("more-routes").disabled = true;
  await searchLane.run(
    request.url,
    { ...request.body, offset: previous.next_offset, data_revision: previous.data_revision },
    (page) => {
      state.result = {
        ...previous,
        query: page.query,
        search_time_seconds: page.search_time_seconds,
        diagnostics: page.diagnostics,
        next_offset: page.next_offset,
        items: [...previous.items, ...page.items],
        airports: [...new Map([...previous.airports, ...page.airports].map(a => [a.iata, a])).values()],
      };
      for (const airport of page.airports) state.airports.set(airport.iata, airport);
      renderOptions();
      $("status").textContent = `${state.result.items.length} journeys loaded${page.next_offset == null ? ". All matching journeys shown." : "."}`;
    },
    error => showError("error", errorMessage(error)),
    () => {
      $("more-routes").disabled = false;
      updateSearchButton();
    },
  );
}
$("more-routes").addEventListener("click", moreRoutes);
function renderOptions() {
  const result = state.result;
  if (!result) return;
  $("more-routes").hidden = result.next_offset == null;
  $("more-routes").disabled = false;
  updateWorkspaceState(!result.items.length);
  $("result-count").textContent = `${result.items.length}${result.next_offset != null ? "+" : ""}`;
  $("search-time").hidden = false;
  $("search-time").textContent = `Search time: ${result.search_time_seconds.toFixed(2)} s${result.query.offset ? " · latest page" : ""}`;
  const v = state.queryValues,
    via = result.query.via_airport,
    viaLabel = Array.isArray(via) ? via.join(" → ") : via;
  $("applied-query").textContent =
    `${result.query.origin} → ${result.query.destination}${viaLabel ? " via " + viaLabel : ""} · ${v.date}${v["last-date"] && v["last-date"] !== v.date ? " to " + v["last-date"] : ""} · ${v["input-clock"] === "UTC" ? "UTC" : result.departure_timezone || result.airports.find((a) => a.iata === result.query.origin)?.timezone || "local"} · ${timeBasisLabel(v["time-basis"])} · ${result.query.max_legs === null ? "Any number of stops" : result.query.max_legs === 1 ? "Nonstop" : `Up to ${result.query.max_legs - 1} stops`}`;
  const d = result.diagnostics;
  const noConnectingPath = d.exhaustive && result.query.max_legs === null,
    connectionWindow =
      result.query.maximum_connection_minutes === null
        ? `at least ${result.query.minimum_connection_minutes} minutes`
        : `${result.query.minimum_connection_minutes}–${result.query.maximum_connection_minutes} minutes`;
  $("partial").hidden = d.exhaustive;
  const reasons = d.termination_reasons.map((x) =>
    x === "data_limit"
      ? "data loading limit"
      : x === "candidate_limit"
        ? "calculation limit"
        : x,
  );
  $("partial").textContent =
    `Search reached its ${reasons.join(" and ")}. ${result.items.length ? "Returned routes are feasible, but their overall ranking is incomplete." : "The ranking is incomplete; a matching route may not have been examined."} Narrow the dates or add filters to reduce the search.`;
  $("options").innerHTML = result.items.length
    ? result.items
        .map((item, index) => {
          const count = item.legs.length,
            codes = journeyAirports(item.legs);
          return `<button class="option" type="button" data-option="${index}" aria-pressed="${!state.selection?.edited && item.itinerary_id === state.selection?.itinerary_id}">
      <span class="option-title"><span><span class="option-rank" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span>${e(stamp(item.departure, zone(item.legs[0].origin_iata, result)).split(",").at(-1).trim())}</span><span>${e(duration(item.duration_minutes))}</span></span>
      <span class="route-codes route-chain" aria-label="${e(codes.join(" → "))}">${routeChain(codes)}</span>
      ${durationChart(item.legs)}
      <span class="option-times">Depart ${e(stamp(item.departure, zone(item.legs[0].origin_iata, result)))}<br>Arrive ${e(stamp(item.arrival, zone(item.legs.at(-1).destination_iata, result)))}</span>
      <span class="option-facts"><span>${count === 1 ? "Nonstop" : `${count - 1} stop${count === 2 ? "" : "s"}`}</span>${count > 1 ? `<span>${item.connections.some((c) => c.airport_change) ? "Airport change" : "Same-airport connections"}</span>` : ""}${qualityBadges(item.legs)}</span>
    </button>`;
        })
        .join("")
    : `<p class="empty">${noConnectingPath ? `No connecting path from ${e(result.query.origin)} to ${e(result.query.destination)} satisfies this date window and filters. Stop count is unrestricted. Connections must be ${connectionWindow}; ${result.query.allow_airport_change ? "only permitted airport transfers are allowed" : "airport changes are off"}. Endpoint flight counts do not guarantee a connecting journey. Try different dates or connection settings.` : d.exhaustive ? "No matching route in loaded data. Try a wider departure date range or fewer filters." : "No route returned before the search stopped early. A matching route may not have been examined."}</p>`;
}
function renderJourney({ preserveView = false } = {}) {
  const selected = state.selection,
    legs = selected?.legs || [];
  const codes = journeyAirports(legs);
  $("map-route-title").textContent = legs.length
    ? `${codes[0]} → ${codes.at(-1)}`
    : "Your route";
  $("map-caption").textContent = legs.length
    ? codes.join(" → ")
    : "Choose a journey to map its route";
  $("journey-summary").innerHTML = legs.length
    ? `<span><b>${e(duration((Date.parse(arrivalTime(legs.at(-1))) - Date.parse(departureTime(legs[0]))) / 60000))}</b>Total journey</span><span><b>${legs.length === 1 ? "Nonstop" : `${legs.length - 1} stop${legs.length === 2 ? "" : "s"}`}</b>Connections</span><span><b>${e(stamp(departureTime(legs[0]), zone(legs[0].origin_iata)).split(",")[0])}</b>Departure</span>`
    : "<span>Select a flight option to see its path and times.</span>";
  $("undo").disabled = !state.history.length;
  $("export").disabled = !legs.length;
  $("journey-status").textContent = !legs.length
    ? "Select a flight option to inspect its legs."
    : `${selected.edited ? "Edited journey · " : ""}${selected.complete ? "Arrives at " + legs.at(-1).destination_iata : "No complete onward route found"} · ${duration((Date.parse(arrivalTime(legs.at(-1))) - Date.parse(departureTime(legs[0]))) / 60000)}${selected.diagnostics?.exhaustive === false ? " · search partial" : ""}`;
  $("legs").innerHTML = legs
    .map((f, i) => {
      const conn = selected.connections[i],
        gap = conn
          ? `<div class="gap ${conn.airport_change ? "airport-change" : ""}"><span>${conn.airport_change ? `${e(conn.arrival_iata)} → ${e(conn.departure_iata)} · airport change` : e(conn.arrival_iata) + " connection"} · ${duration(conn.wait_minutes)}<br><span class="small">Minimum ${conn.minimum_minutes}m</span></span><button type="button" data-edit="${i}">Change connection</button></div>`
          : "";
      return `<div class="leg" data-flight-id="${e(f.flight_id)}"><span class="leg-number">${i + 1}</span><div><div class="leg-route">${e(flightLabel(f))} · ${e(f.origin_iata)} → ${e(f.destination_iata)}</div>
      <div class="leg-time"><div><strong>${e(flightTimeLabel(f, "departure", zone(f.origin_iata)))}</strong>${e(label(f.origin_iata))}</div><div><strong>${e(flightTimeLabel(f, "arrival", zone(f.destination_iata)))}</strong>${e(label(f.destination_iata))}</div></div>
      ${f.aircraft_type_code ? `<div class="leg-source">Aircraft type: ${e(f.aircraft_type_code)}</div>` : ""}
      <div class="leg-quality">${qualityDetails(f)}</div>
      <div class="leg-source">${e(f.status)}</div></div><button type="button" data-focus="${i}">Show leg</button></div>${gap}`;
    })
    .join("");
  // A partial plan still exposes the next gap so the user can continue exploring.
  if (
    legs.length &&
    !selected.complete &&
    (state.result.query.max_legs === null ||
      legs.length < state.result.query.max_legs)
  )
    $("legs").insertAdjacentHTML(
      "beforeend",
      `<div class="gap"><span>Continue from ${e(legs.at(-1).destination_iata)}</span><button type="button" data-edit="${legs.length - 1}">Find onward flights</button></div>`,
    );
  $("time-cursor").disabled = !legs.length;
  if (!preserveView) $("time-cursor").value = 0;
  state.cursor = legs.length ? Date.parse(departureTime(legs[0])) : null;
  if (!preserveView) {
    map.setAirports(selected?.airports || [...state.airports.values()]);
    map.setJourney(legs);
  }
  updateCursor();
  renderDatasetInfo();
}
function updateCursor() {
  const legs = state.selection?.legs || [];
  if (!legs.length) {
    $("cursor-time").textContent = "—";
    $("timeline").replaceChildren();
    return;
  }
  const start = Date.parse(departureTime(legs[0])),
    end = Date.parse(arrivalTime(legs.at(-1)));
  state.cursor =
    start + ((end - start) * Number($("time-cursor").value)) / 1000;
  let detail = "Arrived at " + legs.at(-1).destination_iata;
  for (let i = 0; i < legs.length; i++) {
    const f = legs[i],
      previous = legs[i - 1];
    if (state.cursor < Date.parse(departureTime(f))) {
      detail =
        previous && previous.destination_iata !== f.origin_iata
          ? `Airport change ${previous.destination_iata} → ${f.origin_iata} · next flight ${flightLabel(f)}`
          : `Waiting at ${f.origin_iata} · next flight ${flightLabel(f)}`;
      break;
    }
    if (state.cursor <= Date.parse(arrivalTime(f))) {
      detail = `${flightLabel(f)} · ${f.origin_iata} → ${f.destination_iata}`;
      break;
    }
  }
  $("cursor-time").textContent = stamp(state.cursor, "UTC");
  $("cursor-detail").textContent = `${detail} · journey illustration`;
  map.setCursor(state.cursor);
  drawTimeline($("timeline"), legs, state.cursor);
}
function renderDatasetInfo() {
  const data = state.catalog?.dataset;
  if (!data) return;
  const entries = [
    ["Loaded flights", String(data.total_record_count ?? data.flight_count)],
    ["Searchable flights", String(data.flight_count)],
    ["Flights with accuracy warnings", String(data.needs_review_count || 0)],
    ["Coverage", "May be incomplete"],
  ];
  $("dataset-content").innerHTML =
    `<dl>${entries.map(([a, b]) => `<dt>${e(a)}</dt><dd>${e(b)}</dd>`).join("")}</dl>`;
}
function closeEditor() {
  editLane.cancel();
  state.editing = null;
  state.preview = null;
  $("connection-editor").hidden = true;
  $("next-options").replaceChildren();
  $("more-next").hidden = true;
}
function updateEditorZone() {
  if (state.editing === null) return;
  const f = state.selection.legs[state.editing];
  $("editor-zone").textContent =
    `Not-before time uses ${inputZone(f.destination_iata)}. Connections searched until ${stamp(state.result.query.horizon_end, inputZone(state.result.query.destination))}.`;
}
function openEditor(index) {
  closeEditor();
  state.editing = index;
  editField("minimum").value = state.result.query.minimum_connection_minutes;
  editField("window").value = "any";
  editField("airport-change").checked = state.result.query.allow_airport_change;
  editField("next-airport").value = "";
  updateAirportNames();
  const f = state.selection.legs[index];
  editField("after").value = localInput(
    arrivalTime(f),
    inputZone(f.destination_iata),
  );
  editField("fold").value = "";
  editField("after").required = false;
  $("editor-title").textContent =
    `After ${flightLabel(f)} at ${f.destination_iata}`;
  updateEditorZone();
  $("connection-editor").hidden = false;
  $("after-field").hidden = true;
  $("fold-field").hidden = true;
  showError("editor-error", "");
  $("editor-status").textContent =
    "Earlier flights stay selected. Applying a flight replaces later flights.";
  $("find-next").disabled = false;
  editField("next-airport").focus();
}
function editorRequest(notBefore = null) {
  const mode = editField("window").value;
  return {
    prefix: state.selection.legs.slice(0, state.editing + 1).map(ref),
    destination: state.result.query.destination,
    horizon_end: state.result.query.horizon_end,
    max_legs: state.result.query.max_legs,
    window: {
      minimum_minutes: Number(editField("minimum").value),
      maximum_minutes: mode === "soon" ? 360 : mode === "day" ? 1440 : null,
      not_before: notBefore,
    },
    allow_airport_change: editField("airport-change").checked,
    via_airport: state.result.query.via_airport || null,
    next_airport: airportCode(editField("next-airport").value) || null,
    avoid_airports: state.result.query.avoid_airports,
    trace: false,
    time_basis:
      state.result.query.time_basis ||
      state.selection.legs[0]?.time_basis ||
      "scheduled",
  };
}
$("more-next").addEventListener("click", () =>
  findNext(state.preview?.plan.next_option_offset || 0),
);
async function findNext(optionOffset = 0) {
  if (typeof optionOffset !== "number") optionOffset = 0;
  const previous = optionOffset ? state.preview : null;
  if (state.editing === null) return;
  const mode = editField("window").value,
    after = editField("after").value;
  if (mode === "after" && !after) {
    showError("editor-error", "Choose a not-before date and time.");
    return;
  }
  if (!previous) {
    state.preview = null;
    $("next-options").replaceChildren();
  }
  $("more-next").hidden = true;
  showError("editor-error", "");
  $("find-next").disabled = true;
  $("editor-status").textContent =
    "Finding next flights and complete onward routes…";
  let request = previous
    ? { ...previous.request, option_offset: optionOffset, data_revision: previous.plan.data_revision }
    : editorRequest();
  const fail = (error) => {
    acceptTimeError(error);
    showError("editor-error", errorMessage(error));
    $("editor-status").textContent =
      "Connection search failed. The selected journey is unchanged.";
  };
  const finish = () => {
    $("find-next").disabled = false;
    $("more-next").hidden = state.preview?.plan.next_option_offset == null;
  };
  const receive = (plan) => {
    if (previous)
      plan.options = [...new Map(
        [...previous.plan.options, ...plan.options].map(option => [option.flight.flight_id, option]),
      ).values()];
    $("more-next").hidden = plan.next_option_offset == null;
    state.preview = { request, plan };
    $("editor-status").textContent =
      `${plan.options.length} next-flight options${plan.options_total == null ? "" : ` of ${plan.options_total}`}${plan.next_option_offset != null ? " · more available" : ""}${plan.diagnostics.exhaustive ? "" : " · search partial"}.${plan.options.length ? " Choose a flight to apply." : ""}`;
    $("next-options").innerHTML = plan.options.length
      ? plan.options
          .map((option, i) => {
            const f = option.flight,
              c = option.connection;
            return `<div class="next-option"><div><strong>${e(flightLabel(f))} · ${e(f.origin_iata)} → ${e(f.destination_iata)}</strong><span>${e(stamp(departureTime(f), inputZone(f.origin_iata, plan)))}</span><span>${duration(c.wait_minutes)} wait${c.airport_change ? " · airport change · minimum " + c.minimum_minutes + "m" : ""}</span><span>${option.complete ? "Arrives " + e(stamp(arrivalTime(option.tail.at(-1) || f), inputZone(state.result.query.destination, plan))) : "No complete onward route found"}</span></div><button type="button" data-next="${i}">Apply flight</button></div>`;
          })
          .join("")
      : `<p class="empty">${
          plan.diagnostics.exhaustive
            ? "No next flight matches these filters in loaded data. Try a different airport, a wider departure window, or an airport change."
            : "The search stopped early without returning a next flight. A matching flight may not have been examined."
        }</p>`;
  };
  const rebuild = () =>
    editLane.run("/v1/plans/rebuild", request, receive, fail, finish);
  if (mode === "after") {
    if (field("input-clock").value === "UTC") {
      request.window.not_before = `${after}:00Z`;
      await rebuild();
    } else {
      const airport = state.selection.legs[state.editing].destination_iata;
      const fold =
        editField("fold").value === "" ? null : Number(editField("fold").value);
      // Resolve, then calculate in this lane. Cancelled editors cannot start a rebuild.
      await editLane.run(
        "/v1/time/resolve",
        {
          airport,
          local_time: after,
          fold,
          expected_timezone: inputZone(airport),
        },
        async (resolved) => {
          request.window.not_before = resolved.utc;
          await rebuild();
        },
        fail,
        finish,
      );
    }
  } else await rebuild();
}
async function applyNext(index) {
  if (!state.preview) return;
  const { plan, request } = state.preview,
    option = plan.options[index];
  if (!option) return;
  $("next-options")
    .querySelectorAll("button")
    .forEach((b) => (b.disabled = true));
  $("editor-status").textContent =
    "Rechecking the selected flight and rebuilding later legs…";
  await editLane.run(
    "/v1/plans/rebuild",
    { ...request, chosen_next: ref(option.flight) },
    (updated) => {
      // Save facts, not a row index; undo restores the exact prior selection.
      state.history.push(structuredClone(state.selection));
      if (state.history.length > 10) state.history.shift();
      state.selection = { ...updated, edited: true, itinerary_id: null };
      closeEditor();
      renderOptions();
      renderJourney();
      $("undo").focus();
      $("status").textContent =
        "Connection updated. All later legs were recalculated from current flight facts.";
    },
    (error) => {
      showError("editor-error", errorMessage(error));
      $("editor-status").textContent =
        "Could not apply the flight. The previous journey remains selected.";
    },
    () => {
      $("next-options")
        .querySelectorAll("button")
        .forEach((b) => (b.disabled = false));
    },
  );
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
form.addEventListener("input", (event) => {
  if (!event.target.hasAttribute("data-airport-code")) return;
  userEdited = true;
  normalizeAirportInput(event.target);
  updateZones();
  if ([field("origin"), field("destination")].includes(event.target))
    loadDates();
  if (event.target.hasAttribute("data-airport-list")) {
    updateViaSuggestions(true);
    clearTimeout(viaLookupTimer);
    viaLookupLane.cancel();
    const control = event.target,
      before = control.value.slice(0, control.selectionStart ?? control.value.length),
      token = before.split(/[\s,]+/).at(-1);
    if (token.length >= 2)
      viaLookupTimer = setTimeout(() => viaLookupLane.run(
        `/v1/airports?${new URLSearchParams({ q: token })}`,
        undefined,
        airports => {
          for (const airport of airports) state.airports.set(airport.iata, airport);
          if (document.activeElement === control) updateViaSuggestions(true);
        },
        () => {},
      ), 200);
  }
  closeEditor();
  if (state.result)
    $("status").textContent =
      "Inputs changed. Search to apply them; previous results remain visible.";
});
form.addEventListener("change", (event) => {
  userEdited = true;
  utcReviewPending = false;
  updateZones();
  if (event.target === field("date")) updateLastDates();
  if (event.target === field("time-basis")) {
    timeBasisEdited = true;
    updateDatasetTag();
  }
  loadDates();
  updateSearchButton();
  closeEditor();
  if (state.result)
    $("status").textContent =
      "Inputs changed. Search to apply them; previous results remain visible.";
});
for (const event of ["focus", "click"])
  field("via").addEventListener(event, () => updateViaSuggestions(true));
field("via").addEventListener("blur", hideViaSuggestions);
field("via").addEventListener("keyup", (event) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
    updateViaSuggestions(true);
});
field("via").addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key === "Escape" || event.key === "Tab") {
    hideViaSuggestions();
  } else if (event.key === "Enter" && viaActive >= 0) {
    event.preventDefault();
    chooseVia(viaActive);
  } else if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    if ($("via-suggestions").hidden) updateViaSuggestions(true);
    if (!viaOptions.length) return;
    event.preventDefault();
    const down = event.key === "ArrowDown";
    viaActive =
      viaActive < 0
        ? down
          ? 0
          : viaOptions.length - 1
        : (viaActive + (down ? 1 : viaOptions.length - 1)) % viaOptions.length;
    const options = $("via-suggestions").children;
    for (const [index, option] of [...options].entries())
      option.setAttribute("aria-selected", String(index === viaActive));
    const active = options[viaActive];
    field("via").setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  }
});
$("via-suggestions").addEventListener("mousedown", (event) =>
  event.preventDefault(),
);
$("via-suggestions").addEventListener("click", (event) => {
  const option = event.target.closest("[data-via-option]");
  if (option) {
    event.preventDefault();
    chooseVia(Number(option.dataset.viaOption));
  }
});
$("swap").addEventListener("click", () => {
  const origin = field("origin").value;
  field("origin").value = field("destination").value;
  field("destination").value = origin;
  form.dispatchEvent(new Event("change", { bubbles: true }));
});
$("options").addEventListener("click", (event) => {
  const button = event.target.closest("[data-option]");
  if (!button) return;
  closeEditor();
  const option = state.result.items[Number(button.dataset.option)];
  state.selection = {
    ...option,
    airports: state.result.airports,
    diagnostics: state.result.diagnostics,
    complete: true,
    edited: false,
  };
  state.history = [];
  renderOptions();
  renderJourney();
  if (matchMedia("(max-width:720px)").matches) {
    setView("journey");
    $("export").focus();
  } else
    $("options")
      .querySelector(`[data-option="${button.dataset.option}"]`)
      ?.focus();
});
$("legs").addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit]"),
    focus = event.target.closest("[data-focus]");
  if (edit) openEditor(Number(edit.dataset.edit));
  if (focus) {
    setView("map");
    map.focus(Number(focus.dataset.focus));
    syncProjection();
    if (matchMedia("(max-width:720px)").matches) $("fit-map").focus();
  }
});
editorForm.addEventListener("submit", (event) => {
  event.preventDefault();
  findNext();
});
function editorChanged() {
  editLane.cancel();
  state.preview = null;
  $("next-options").replaceChildren();
  $("find-next").disabled = false;
  const after = editField("window").value === "after";
  $("after-field").hidden = !after;
  $("fold-field").hidden = !after || field("input-clock").value === "UTC";
  editField("after").required = after;
  $("editor-status").textContent =
    "Connection filters changed. Find next flights to update the options.";
  showError("editor-error", "");
}
editorForm.addEventListener("input", (event) => {
  if (!event.target.hasAttribute("data-airport-code")) return;
  normalizeAirportInput(event.target);
  updateAirportNames();
  editorChanged();
});
editorForm.addEventListener("change", editorChanged);
$("next-options").addEventListener("click", (event) => {
  const button = event.target.closest("[data-next]");
  if (button) applyNext(Number(button.dataset.next));
});
$("close-editor").addEventListener("click", () => {
  const index = state.editing;
  closeEditor();
  $("legs").querySelector(`[data-edit="${index}"]`)?.focus();
});
$("undo").addEventListener("click", () => {
  if (!state.history.length) return;
  closeEditor();
  state.selection = state.history.pop();
  renderOptions();
  renderJourney();
  $("status").textContent =
    "Previous journey restored from its saved facts. Search again to refresh flight data.";
});
$("display-clock").addEventListener("change", () => {
  renderOptions();
  renderJourney({ preserveView: true });
});
$("time-cursor").addEventListener("input", updateCursor);
new ResizeObserver(() =>
  drawTimeline($("timeline"), state.selection?.legs, state.cursor),
).observe($("timeline"));
function syncProjection() {
  $("globe-mode").setAttribute("aria-pressed", String(map.mode === "globe"));
  $("flat-mode").setAttribute("aria-pressed", String(map.mode === "flat"));
  for (const id of [
    "rotate-left",
    "rotate-right",
    "tilt-up",
    "tilt-down",
    "zoom-in",
    "zoom-out",
  ])
    $(id).disabled = map.mode !== "globe";
}
function setView(view) {
  document.querySelector(".workspace").dataset.view = view;
  document.querySelector("main").dataset.view = view;
  document
    .querySelectorAll(".mobile-views button")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.view === view)),
    );
}
document.querySelector(".mobile-views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) {
    setView(button.dataset.view);
    if (button.dataset.view === "map")
      document.querySelector(".map-panel").scrollIntoView({ block: "start" });
  }
});
for (const [id, mode] of [
  ["globe-mode", "globe"],
  ["flat-mode", "flat"],
])
  $(id).addEventListener("click", () => {
    map.setMode(mode);
    syncProjection();
  });
for (const [id, degrees] of [
  ["rotate-left", -25],
  ["rotate-right", 25],
])
  $(id).addEventListener("click", () => map.rotate(degrees));
for (const [id, degrees] of [
  ["tilt-up", -25],
  ["tilt-down", 25],
])
  $(id).addEventListener("click", () => map.tilt(degrees));
for (const [id, factor] of [
  ["zoom-in", 1.25],
  ["zoom-out", 0.8],
])
  $(id).addEventListener("click", () => map.adjustZoom(factor));
$("fit-map").addEventListener("click", () => map.fit());
$("copy-search").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(
      queryLink(state.queryValues || values()),
    );
    $("status").textContent =
      "Search link copied. It reruns the query against the receiver’s loaded data.";
  } catch {
    $("status").textContent =
      "Copy the address from your browser after running the search.";
  }
});
$("export").addEventListener("click", () => {
  const payload = {
    format: "flyji-journey-2",
    data_licence_notice: `${location.origin}/data-sources`,
    exported_at: new Date().toISOString(),
    query: state.result.query,
    query_inputs: state.queryValues,
    selection: state.selection,
    coverage: "unknown",
    time_basis:
      state.result.query.time_basis ||
      state.selection.legs[0]?.time_basis ||
      "scheduled",
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2) + "\n"], {
      type: "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "flyji-journey.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function loadCatalog() {
  await catalogLane.run(
    "/v1/catalog?include_airports=false",
    undefined,
    (catalog) => {
      state.catalog = catalog;
      updateDatasetTag();
      renderDatasetInfo();
    },
    () => {
      $("dataset-tag").textContent =
        "Dataset statistics unavailable; search remains available.";
    },
  );
}
async function loadBootstrap() {
  $("retry-catalog").hidden = true;
  await bootstrapLane.run(
    "/v1/bootstrap",
    undefined,
    async (bootstrap) => {
      for (const airport of bootstrap.airports)
        state.airports.set(airport.iata, airport);
      map.setAirports([...state.airports.values()]);
      $("airport-suggestions").innerHTML = bootstrap.airports
        .map((a) => `<option value="${e(a.iata)}" label="${e(a.name || a.iata)}"></option>`)
        .join("");
      if (!timeBasisEdited && !linkedValues["time-basis"])
        field("time-basis").value = bootstrap.default_time_basis;
      for (const option of field("time-basis").options)
        option.disabled = !bootstrap.available_time_bases.includes(
          option.value,
        );
      updateZones();
      updateDatasetTag();
      if (!linkedUTC || userEdited) {
        datesKey = null;
        await loadDates();
        if (!userEdited && linkedSearch && availableDates.size &&
            availability?.outbound && availability?.inbound)
          search();
      }
    },
    (error) => {
      showError("error", errorMessage(error));
      $("status").textContent =
        "Airport details unavailable. Retry or search using UTC dates.";
      $("retry-catalog").hidden = false;
    },
  );
}
$("retry-catalog").addEventListener("click", loadBootstrap);
map
  .load()
  .catch((error) =>
    showError(
      "map-error",
      `${error.message} Flight details and connection editing remain available.`,
    ),
  );
// A shared UTC query is already complete. Availability configures the calendars;
// it must not delay sending that query to the authoritative search endpoint.
const linkedValues = Object.fromEntries(new URLSearchParams(location.search));
const linkedSearch =
  [linkedValues.origin, linkedValues.destination].every((code) => /^[a-z]{3}$/i.test(code || "")) &&
  /^\d{4}-\d{2}-\d{2}$/.test(linkedValues.date || "");
const linkedUTC = linkedSearch && linkedValues["input-clock"] === "UTC" &&
  ["scheduled", "observed", "actual"].includes(linkedValues["time-basis"]);
restore(linkedValues);
updateZones();
if (linkedUTC) {
  const initialSearch = search({ checkAvailability: false });
  const initialDates = loadDates();
  // Keep bootstrap out of the two interactive slots until both initial reads
  // finish, and leave archive-wide statistics off the first-result path.
  initialSearch.then(loadCatalog);
  Promise.all([initialSearch, initialDates]).then(loadBootstrap);
} else {
  loadBootstrap();
  loadCatalog();
  if (!linkedSearch) loadDates();
}
