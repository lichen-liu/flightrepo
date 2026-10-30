import {
  arrivalTime,
  departureTime,
  duration,
  escapeHTML as e,
  flightLabel,
  flightTimeLabel,
  stampParts as clockParts,
} from "./format.mjs";
import { qualityLabels } from "./quality.mjs";

export function renderCompactEndpoint(flight, event, zone = "UTC") {
  const departure = event === "departure",
    airport = departure ? flight.origin_iata : flight.destination_iata,
    instant = departure ? departureTime(flight) : arrivalTime(flight),
    { date, clock, offset } = clockParts(instant, zone),
    observed = flight.time_basis === "observed",
    full = flightTimeLabel(flight, event, zone);
  return `<span class="option-endpoint"><time datetime="${e(instant)}" title="${e(full)}" aria-label="${e(full)}">${observed ? "≈ " : ""}${e(clock)}</time><b>${e(airport)}</b><small>${e(date)} · ${e(offset)}</small></span>`;
}

export function journeyElapsedLabel(legs) {
  if (!legs.length) return duration(0);
  const first = legs[0], last = legs.at(-1),
    maximum = Math.max(0, (Date.parse(arrivalTime(last)) - Date.parse(departureTime(first))) / 60000),
    observed = first.time_basis === "observed" || last.time_basis === "observed";
  if (!observed) return duration(maximum);
  const minimum = Math.max(0, (
    Date.parse(last.arrival_lower || arrivalTime(last)) -
    Date.parse(first.departure_upper || departureTime(first))
  ) / 60000);
  return duration(minimum) === duration(maximum)
    ? `≈ ${duration(maximum)}`
    : `${duration(minimum)}–${duration(maximum)}`;
}

function endpoint(flight, event, zone, label) {
  const departure = event === "departure",
    airport = departure ? flight.origin_iata : flight.destination_iata,
    instant = departure ? departureTime(flight) : arrivalTime(flight),
    lower = flight[`${event}_lower`] || instant,
    upper = flight[`${event}_upper`] || instant,
    timezone = zone(airport) || "UTC",
    first = clockParts(lower, timezone),
    last = clockParts(upper, timezone),
    observed = flight.time_basis === "observed",
    hasWindow = observed && lower !== upper,
    shown = observed
      ? hasWindow ? `${first.clock}–${last.clock}` : `≈ ${first.clock}`
      : first.clock,
    full = flightTimeLabel(flight, event, timezone);
  return `<div class="detail-station">
    <span class="detail-event">${departure ? "Depart" : "Arrive"}</span>
    <div class="detail-place"><strong class="detail-airport">${e(airport)}</strong><time class="detail-clock" datetime="${e(lower)}" title="${e(full)}">${e(shown)}</time></div>
    <span class="detail-airport-name">${e(label(airport) || airport)}</span>
    <span class="detail-date">${e(first.date)}<span class="detail-zone">${e(first.offset)}</span></span>
    ${observed ? `<span class="detail-observed-window">${e(full)}</span>` : ""}
  </div>`;
}

function flightDuration(flight) {
  const maximum = Math.max(0, (Date.parse(arrivalTime(flight)) - Date.parse(departureTime(flight))) / 60000);
  if (flight.time_basis !== "observed") return `${duration(maximum)} flight`;
  const minimum = Math.max(0, (
    Date.parse(flight.arrival_lower || arrivalTime(flight)) -
    Date.parse(flight.departure_upper || departureTime(flight))
  ) / 60000);
  return duration(minimum) === duration(maximum)
    ? `≈ ${duration(maximum)} flight`
    : `${duration(minimum)}–${duration(maximum)} observed duration`;
}

function qualityMarkup(labels) {
  return labels.map((item) => `<div class="quality-detail quality-${e(item.severity)}"><strong>${e(item.label)}</strong> · ${e(item.detail)}</div>`).join("");
}

function connectionPause(connection, index, observed) {
  const change = connection.airport_change,
    airport = change
      ? `${connection.arrival_iata} → ${connection.departure_iata}`
      : connection.arrival_iata;
  return `<div class="gap${change ? " airport-change" : ""}">
    <span class="detail-stop-mark" aria-hidden="true">${change ? "⇄" : "·"}</span>
    <div class="detail-stop-copy"><span class="detail-stop-title">${change ? "Change airports" : "Connection at"} <strong>${e(airport)}</strong></span><strong class="detail-stop-duration">${observed ? "At least " : ""}${e(duration(connection.wait_minutes))} ${change ? "between flights" : "to connect"}</strong><span class="detail-stop-meta">Minimum ${e(connection.minimum_minutes)}m${change ? " · Includes the airport transfer" : ""}</span></div>
    <button type="button" data-edit="${index}">Change connection</button>
  </div>`;
}

export function renderJourneyLegs(legs, connections, { zone = () => "UTC", label = (code) => code } = {}) {
  return legs.map((flight, index) => {
    const caution = qualityLabels([flight]),
      information = qualityLabels([flight], { details: true }).filter((item) => item.severity === "info"),
      connection = index < legs.length - 1 ? connections[index] : null;
    return `<div class="leg" data-flight-id="${e(flight.flight_id)}">
      <span class="leg-number">${index + 1}</span>
      <div class="detail-flight-content">
        <div class="detail-flight-head"><span class="leg-route">${e(flightLabel(flight))}</span><span class="detail-flight-duration">${e(flightDuration(flight))}</span></div>
        <div class="detail-stations">${endpoint(flight, "departure", zone, label)}${endpoint(flight, "arrival", zone, label)}</div>
        ${caution.length ? `<div class="leg-quality">${qualityMarkup(caution)}</div>` : ""}
        <details class="detail-evidence"><summary>Flight details</summary>${flight.aircraft_type_code ? `<div class="leg-source">Aircraft type: ${e(flight.aircraft_type_code)}</div>` : ""}${flight.status ? `<div class="leg-source">Status: ${e(flight.status)}</div>` : ""}${qualityMarkup(information)}</details>
      </div>
      <button type="button" data-focus="${index}">Show leg</button>
    </div>${connection ? connectionPause(connection, index, [flight, legs[index + 1]].some((leg) => leg.time_basis === "observed")) : ""}`;
  }).join("");
}
