import assert from "node:assert/strict";
import test from "node:test";
import { flightTimeLabel } from "../src/server/web/format.mjs";
import { journeyElapsedLabel, renderCompactEndpoint, renderJourneyLegs } from "../src/server/web/journey-detail.mjs";

const flight = (changes = {}) => ({
  flight_id: "one",
  flight_number: "BA 92",
  origin_iata: "YYZ",
  destination_iata: "LHR",
  departure_scheduled: "2026-10-17T18:00:00Z",
  arrival_scheduled: "2026-10-18T01:00:00Z",
  status: "scheduled",
  ...changes,
});

const settings = {
  zone: (airport) => ({ YYZ: "America/Toronto", LHR: "Europe/London" })[airport] || "UTC",
  label: (airport) => ({ YYZ: "Toronto Pearson", LHR: "London Heathrow" })[airport] || airport,
};

test("leg endpoints pair local clocks with their own date and UTC offset across midnight", () => {
  const html = renderJourneyLegs([flight()], [], settings);
  assert.match(html, /class="detail-airport">YYZ<\/strong><time[^>]+>14:00<\/time>/);
  assert.match(html, /class="detail-airport">LHR<\/strong><time[^>]+>02:00<\/time>/);
  assert.match(html, /17 Oct 2026<span class="detail-zone">GMT-4/);
  assert.match(html, /18 Oct 2026<span class="detail-zone">GMT\+1/);
  assert.match(html, /7h flight/);
  assert.match(html, /Toronto Pearson/);
  assert.match(html, /data-flight-id="one"/);
});

test("observed windows retain both dates offsets and uncertainty as visible text", () => {
  const observed = flight({
    time_basis: "observed",
    departure_lower: "2026-10-17T23:55:00Z",
    departure_upper: "2026-10-18T00:10:00Z",
    arrival_lower: "2026-10-18T06:50:00Z",
    arrival_upper: "2026-10-18T07:10:00Z",
  });
  const html = renderJourneyLegs([observed], []);
  assert.match(html, /23:55–00:10/);
  assert.ok(html.includes(`<span class="detail-observed-window">${flightTimeLabel(observed, "departure", "UTC")}</span>`));
  assert.ok(html.includes(flightTimeLabel(observed, "arrival", "UTC")));
  assert.match(html, /6h 40m–7h 15m observed duration/);
  const point = { ...observed, departure_upper: observed.departure_lower };
  assert.match(renderJourneyLegs([point], []), /≈ 23:55/);
});

test("compact observed clocks mark conservative bounds and expose both DST offsets accessibly", () => {
  const observed = flight({
    time_basis: "observed",
    departure_lower: "2026-11-01T05:55:00Z",
    departure_upper: "2026-11-01T06:10:00Z",
    arrival_lower: "2026-11-01T12:50:00Z",
    arrival_upper: "2026-11-01T13:10:00Z",
  });
  const html = renderCompactEndpoint(observed, "departure", "America/Toronto"),
    description = flightTimeLabel(observed, "departure", "America/Toronto");
  assert.match(html, />≈ 01:55<\/time>/);
  assert.ok(html.includes(`title="${description}" aria-label="${description}"`));
  assert.match(description, /01:55 GMT-4 – 1 Nov 2026, 01:10 GMT-5/);
  assert.match(html, /<b>YYZ<\/b><small>1 Nov 2026 · GMT-4/);
  const arrival = renderCompactEndpoint(observed, "arrival", "UTC");
  assert.match(arrival, /datetime="2026-11-01T13:10:00Z"/);
  assert.match(arrival, />≈ 13:10<\/time>/);
  assert.ok(arrival.includes(flightTimeLabel(observed, "arrival", "UTC")));
});

test("compact published and actual clocks remain exact and airport markup stays escaped", () => {
  const scheduled = renderCompactEndpoint(flight(), "departure", "America/Toronto");
  assert.match(scheduled, />14:00<\/time>/);
  assert.ok(!scheduled.includes("≈"));
  assert.match(scheduled, /17 Oct 2026 · GMT-4/);
  const actual = renderCompactEndpoint(flight({
    time_basis: "actual", origin_iata: '<img src=x onerror="bad">',
    departure_lower: "2026-10-17T18:10:00Z",
    departure_upper: "2026-10-17T18:10:00Z",
  }), "departure");
  assert.match(actual, />18:10<\/time>/);
  assert.ok(!actual.includes("≈"));
  assert.ok(!actual.includes("<img"));
  assert.ok(actual.includes("&lt;img src=x onerror=&quot;bad&quot;&gt;"));
});

test("journey elapsed labels preserve endpoint uncertainty without inventing interior uncertainty", () => {
  const first = flight({
    time_basis: "observed",
    departure_lower: "2026-10-17T18:00:00Z",
    departure_upper: "2026-10-17T18:15:00Z",
  });
  const last = flight({
    flight_id: "last", origin_iata: "LHR", destination_iata: "CDG", time_basis: "observed",
    arrival_lower: "2026-10-18T07:45:00Z",
    arrival_upper: "2026-10-18T08:15:00Z",
  });
  assert.equal(journeyElapsedLabel([first, last]), "13h 30m–14h 15m");
  assert.equal(journeyElapsedLabel([flight({time_basis:"observed"})]), "≈ 7h");
  assert.equal(journeyElapsedLabel([flight()]), "7h");
  assert.equal(journeyElapsedLabel([flight({time_basis:"actual"})]), "7h");
  assert.equal(journeyElapsedLabel([flight(), first, flight({arrival_scheduled:"2026-10-18T08:00:00Z"})]), "14h");
  assert.equal(journeyElapsedLabel([]), "0m");
});

test("connection pauses preserve edit indices and make airport changes explicit", () => {
  const legs = [flight(), flight({ flight_id: "two", origin_iata: "LGW", destination_iata: "CDG", departure_scheduled: "2026-10-18T07:00:00Z", arrival_scheduled: "2026-10-18T08:00:00Z" })];
  const connection = { arrival_iata: "LHR", departure_iata: "LGW", airport_change: true, wait_minutes: 360, minimum_minutes: 180 };
  const html = renderJourneyLegs(legs, [connection], settings);
  assert.match(html, /class="gap airport-change"/);
  assert.match(html, /Change airports <strong>LHR → LGW/);
  assert.match(html, /6h between flights/);
  assert.match(html, /Minimum 180m · Includes the airport transfer/);
  assert.equal((html.match(/data-edit="0"/g) || []).length, 1);
  assert.ok(!html.includes('data-edit="1"'));
  assert.match(html, /data-focus="0">Show leg/);
  assert.match(html, /data-focus="1">Show leg/);
  const sameAirport = { ...connection, airport_change: false, departure_iata: "LHR" };
  assert.match(renderJourneyLegs(legs, [sameAirport]), /Connection at <strong>LHR/);
});

test("observed connection pauses identify guaranteed minimum waits on either adjacent leg", () => {
  const first = flight(), second = flight({flight_id:"two",origin_iata:"LHR",destination_iata:"CDG"}),
    connection = {arrival_iata:"LHR",departure_iata:"LHR",airport_change:false,wait_minutes:120,minimum_minutes:45};
  for (const legs of [
    [{...first,time_basis:"observed"},second],
    [first,{...second,time_basis:"observed"}],
  ]) assert.match(renderJourneyLegs(legs, [connection]), /At least 2h to connect/);
  assert.ok(!renderJourneyLegs([first,second],[connection]).includes("At least"));
  assert.match(renderJourneyLegs([{...first,time_basis:"observed"},second], [{...connection,airport_change:true,departure_iata:"LGW"}]), /At least 2h between flights/);
});

test("visible warnings survive while routine metadata is disclosed and untrusted content is escaped", () => {
  const html = renderJourneyLegs([flight({
    flight_id: '" onclick="bad',
    flight_number: "<script>bad()</script>",
    aircraft_type_code: "B738",
    quality: { labels: [
      { code: "warning", severity: "warning", label: "Timing conflict", detail: "Possible <bad> timing" },
      { code: "info", severity: "info", label: "Historical observation", detail: "Observed itinerary" },
    ] },
  })], [], { label: () => "<img src=x>" });
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes("<img src=x>"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes('data-flight-id="&quot; onclick=&quot;bad"'));
  const evidence = html.indexOf('<details class="detail-evidence">');
  assert.ok(html.indexOf("Timing conflict") < evidence);
  assert.ok(html.indexOf("Historical observation") > evidence);
  assert.ok(html.indexOf("Status: scheduled") > evidence);
  assert.ok(html.indexOf("Aircraft type: B738") > evidence);
  assert.match(html, /class="leg-quality"/);
});

test("empty legs and a retained partial prefix do not invent a next connection", () => {
  assert.equal(renderJourneyLegs([], []), "");
  const html = renderJourneyLegs([flight()], [{ wait_minutes: 60 }]);
  assert.ok(!html.includes('data-edit="0"'));
});
