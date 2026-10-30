import {
  escapeHTML as e,
  departureTime,
  arrivalTime,
  duration,
} from "./format.mjs";

export function journeyAirports(legs) {
  return legs.length
    ? [
        legs[0].origin_iata,
        ...legs.flatMap((flight, i) =>
          i && legs[i - 1].destination_iata !== flight.origin_iata
            ? [flight.origin_iata, flight.destination_iata]
            : [flight.destination_iata],
        ),
      ]
    : [];
}
export function routeChain(codes) {
  return codes
    .map(
      (code, i) =>
        `<span class="route-node${i === 0 || i === codes.length - 1 ? " endpoint" : ""}"><i aria-hidden="true"></i><b>${e(code)}</b></span>`,
    )
    .join("");
}
export function journeyTiming(legs) {
  if (!legs.length) return { total: 0, flying: 0, connecting: 0 };
  const total = Math.max(
    0,
    (Date.parse(arrivalTime(legs.at(-1))) - Date.parse(departureTime(legs[0]))) / 60000,
  );
  const flying = legs.reduce((sum, flight) => sum + Math.max(
    0,
    (Date.parse(arrivalTime(flight)) - Date.parse(departureTime(flight))) / 60000,
  ), 0);
  return { total, flying, connecting: Math.max(0, total - flying) };
}
export function durationChart(legs, scaleMinutes = journeyTiming(legs).total) {
  if (!legs.length) return "";
  const start = Date.parse(departureTime(legs[0])),
    timing = journeyTiming(legs),
    scale = Math.max(scaleMinutes, timing.total) * 60000;
  if (!(scale > 0)) return "";
  const position = (instant) => ((Date.parse(instant) - start) / scale) * 300;
  const bars = legs
    .map((flight, index) => {
      const left = position(departureTime(flight)),
        right = position(arrivalTime(flight)),
        previous = legs[index - 1],
        gapStart = previous ? position(arrivalTime(previous)) : left;
      const gap = left > gapStart
        ? `<rect class="connection-duration" x="${gapStart}" y="1" width="${left - gapStart}" height="6"><title>${e(previous.destination_iata)} connection · ${[previous, flight].some((leg) => leg.time_basis === "observed") ? "at least " : ""}${e(duration((left - gapStart) * scale / 300 / 60000))}</title></rect>`
        : "";
      return `${gap}<rect class="flight-duration" x="${left}" y="1" width="${Math.max(0, right - left)}" height="6" rx="2"><title>${e(flight.origin_iata)} → ${e(flight.destination_iata)} · ${flight.time_basis === "observed" ? "up to " : ""}${e(duration((right - left) * scale / 300 / 60000))}</title></rect>`;
    })
    .join("");
  const observed = legs.some((flight) => flight.time_basis === "observed");
  return `<svg class="option-timeline" viewBox="0 0 300 8" preserveAspectRatio="none" role="img" aria-label="${observed ? "Observed time bounds. Up to " : ""}${e(duration(timing.total))} journey: ${observed ? "up to " : ""}${e(duration(timing.flying))} flying, ${observed ? "at least " : ""}${e(duration(timing.connecting))} connecting. Shared scale: ${e(duration(scale / 60000))}."><rect class="comparison-track" width="300" height="6" y="1" rx="2"/>${bars}</svg>`;
}
