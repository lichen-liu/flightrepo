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
export function durationChart(legs) {
  if (!legs.length) return "";
  const start = Date.parse(departureTime(legs[0])),
    end = Date.parse(arrivalTime(legs.at(-1))),
    total = end - start;
  if (!(total > 0)) return "";
  const bars = legs
    .map((flight) => {
      const left = ((Date.parse(departureTime(flight)) - start) / total) * 300;
      const width =
        ((Date.parse(arrivalTime(flight)) - Date.parse(departureTime(flight))) /
          total) *
        300;
      return `<rect x="${left}" y="1" width="${Math.max(0, width)}" height="6" rx="2"><title>${e(flight.origin_iata)} → ${e(flight.destination_iata)} · ${duration(((width / 300) * total) / 60000)}</title></rect>`;
    })
    .join("");
  return `<svg class="option-timeline" viewBox="0 0 300 8" preserveAspectRatio="none" role="img" aria-label="Flight and connection durations · ${e(duration(total / 60000))}"><rect class="connection-duration" width="300" height="6" y="1" rx="2"/>${bars}</svg>`;
}
