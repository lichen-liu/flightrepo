export const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const ref = (flight) => ({
  flight_id: flight.flight_id,
});
export const departureTime = (f) => f.departure_lower ?? f.departure_scheduled ?? f.departure_observed_lower ?? f.departure_actual;
export const arrivalTime = (f) => f.arrival_upper ?? f.arrival_scheduled ?? f.arrival_observed_upper ?? f.arrival_actual;
export const flightLabel = (f) => {
  const identity = f.flight_number || (f.callsign ? `${f.callsign} (callsign)` : "Flight");
  return f.operator_name ? `${identity} · ${f.operator_name} (operator)` : identity;
};
export function flightTimeLabel(f, event, zone = "UTC") {
  const point = event === "departure" ? departureTime(f) : arrivalTime(f);
  const lower = f[`${event}_lower`] || point, upper = f[`${event}_upper`] || point;
  const first = stamp(lower, zone), last = stamp(upper, zone);
  if (f.time_basis !== "observed") return first;
  return `${first === last ? "≈ " + first : first + " – " + last} · observed`;
}
export function airportList(value) {
  const codes = value.toUpperCase().split(/[\s,]+/).filter(Boolean);
  for (const code of codes)
    if (!/^[A-Z]{3}$/.test(code))
      throw Error(
        `Invalid airport code: ${code}. Enter three-letter codes separated by commas or spaces.`,
      );
  if (new Set(codes).size !== codes.length)
    throw Error("Via airports must not repeat.");
  if (codes.length > 6) throw Error("Enter at most six via airports.");
  return codes;
}
export function airportListSuggestions(value, position, codes) {
  let start = position,
    end = position;
  while (start > 0 && !/[\s,]/.test(value[start - 1])) start--;
  while (end < value.length && !/[\s,]/.test(value[end])) end++;
  const before = value.slice(0, start),
    after = value.slice(end),
    token = value.slice(start, end).toUpperCase(),
    otherCodes = new Set((before + " " + after).toUpperCase().split(/[\s,]+/));
  return codes
    .filter((code) => !otherCodes.has(code) && code.startsWith(token))
    .map((code) => ({ code, value: before + code + after, caret: start + code.length }));
}
export function duration(minutes) {
  const m = Math.max(0, Math.floor(minutes)),
    days = Math.floor(m / 1440),
    hours = Math.floor((m % 1440) / 60),
    rest = m % 60;
  return [days && `${days}d`, hours && `${hours}h`, (rest || !m) && `${rest}m`]
    .filter(Boolean)
    .join(" ");
}
function localParts(instant, zone, includeClock = false) {
  const options = {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(includeClock
      ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
      : {}),
  };
  return Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", options)
      .formatToParts(new Date(instant))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
}
const dateFromParts = (parts) => `${parts.year}-${parts.month}-${parts.day}`;
export function localDate(instant, zone = "UTC") {
  return dateFromParts(localParts(instant, zone));
}
export function localInput(instant, zone = "UTC") {
  const parts = localParts(instant, zone, true);
  return `${dateFromParts(parts)}T${parts.hour}:${parts.minute}`;
}
export function stamp(instant, zone = "UTC") {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: zone,
    year: "numeric",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZoneName: "shortOffset",
  }).format(new Date(instant));
}
export function addDate(date, days) {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
export function queryLink(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value !== "" && value != null) params.set(key, String(value));
  return `${location.origin}/?${params}`;
}
export function errorMessage(error) {
  const detail = error?.detail;
  if (Array.isArray(detail))
    return detail
      .map((x) => `${x.loc?.slice(1).join(".") || "Input"}: ${x.msg}`)
      .join(" · ");
  return (
    detail?.message ||
    (typeof detail === "string" ? detail : error?.message) ||
    "Could not read the flight data. Retry the request."
  );
}

// Latest response wins even when transport cancellation arrives too late.
export class RequestLane {
  constructor(onActivity = () => () => {}) { this.onActivity = onActivity; }
  generation = 0;
  controller = null;
  cancel() {
    this.finishActivity?.();
    this.finishActivity = null;
    this.generation++;
    this.controller?.abort();
    this.controller = null;
  }
  async run(url, body, receive, failed, finished = () => {}) {
    this.cancel();
    const generation = this.generation;
    const finishActivity = this.onActivity(url);
    this.finishActivity = finishActivity;
    const controller = (this.controller = new AbortController());
    try {
      const response = await fetch(url, {
        signal: controller.signal,
        method: body === undefined ? "GET" : "POST",
        ...(body === undefined
          ? {}
          : {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }),
      });
      const payload = await response.json();
      if (generation !== this.generation) return;
      if (!response.ok) throw payload;
      await receive(payload);
    } catch (error) {
      if (generation === this.generation && error?.name !== "AbortError")
        await failed(error);
    } finally {
      finishActivity();
      if (generation === this.generation) {
        this.controller = null;
        this.finishActivity = null;
        finished();
      }
    }
  }
}
