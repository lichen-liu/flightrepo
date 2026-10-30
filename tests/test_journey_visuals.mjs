import assert from "node:assert/strict";
import test from "node:test";
import {
  durationChart,
  journeyTiming,
} from "../src/server/web/journey-visuals.mjs";

const flight = (origin, destination, departure, arrival) => ({
  origin_iata: origin,
  destination_iata: destination,
  departure_scheduled: `2026-10-17T${departure}:00Z`,
  arrival_scheduled: `2026-10-17T${arrival}:00Z`,
});

test("comparison bars preserve the same minute-to-pixel scale across journeys", () => {
  const shorter = [flight("AAA", "BBB", "10:00", "12:00")],
    longer = [flight("AAA", "BBB", "10:00", "14:00")];
  const shortChart = durationChart(shorter, 240),
    longChart = durationChart(longer, 240);
  assert.match(shortChart, /class="flight-duration" x="0" y="1" width="150"/);
  assert.match(longChart, /class="flight-duration" x="0" y="1" width="300"/);
  assert.match(shortChart, /Shared scale: 4h/);
  assert.match(longChart, /Shared scale: 4h/);
});

test("connection time is visible as a gap in the correct place and included in the burden", () => {
  const legs = [
    flight("AAA", "BBB", "10:00", "11:00"),
    flight("BBB", "CCC", "13:00", "14:00"),
  ];
  assert.deepEqual(journeyTiming(legs), { total: 240, flying: 120, connecting: 120 });
  const chart = durationChart(legs, 240);
  assert.match(chart, /class="connection-duration" x="75" y="1" width="150"/);
  assert.match(chart, /4h journey: 2h flying, 2h connecting/);
  assert.match(chart, /BBB connection · 2h/);
});

test("empty journeys have no timeline and airport metadata is escaped", () => {
  assert.equal(durationChart([]), "");
  assert.deepEqual(journeyTiming([]), { total: 0, flying: 0, connecting: 0 });
  const chart = durationChart([flight("<script>", "BBB", "10:00", "11:00")]);
  assert.ok(!chart.includes("<script>"));
  assert.ok(chart.includes("&lt;script&gt;"));
  assert.match(chart, /1h flying, 0m connecting/);
});

test("observed comparison bars describe upper duration bounds and minimum connections", () => {
  const legs = [
    { ...flight("AAA", "BBB", "10:00", "11:00"), time_basis: "observed" },
    flight("BBB", "CCC", "13:00", "14:00"),
  ];
  const chart = durationChart(legs);
  assert.match(chart, /Observed time bounds\. Up to 4h journey: up to 2h flying, at least 2h connecting/);
  assert.match(chart, /AAA → BBB · up to 1h/);
  assert.match(chart, /BBB connection · at least 2h/);
  assert.match(chart, /BBB → CCC · 1h/);
});
