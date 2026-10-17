import assert from "node:assert/strict";
import test from "node:test";
import { calendarDays } from "../src/server/web/calendar.mjs";
import {
  escapeHTML,
  flightLabel,
  localDate,
  localInput,
  RequestLane,
  airportList,
  airportListSuggestions,
} from "../src/server/web/format.mjs";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("calendar disables gaps and handles leap years without using the browser timezone", () => {
  const available = new Set(["2024-02-01", "2024-02-29"]),
    days = calendarDays("2024-02", available);
  assert.equal(days.length, 29);
  assert.deepEqual(days.filter((day) => day.enabled).map((day) => day.date), [...available]);
  assert.equal(calendarDays("2025-02", new Set()).length, 28);
  assert.equal(days[15].enabled, false);
});

test("via lists preserve travel order across whitespace, commas and trailing separators", () => {
  assert.deepEqual(airportList(" ams,  lhr ,,\n"), ["AMS", "LHR"]);
  assert.deepEqual(airportList("lhr ams"), ["LHR", "AMS"]);
  assert.deepEqual(airportList(""), []);
  assert.deepEqual(airportList("xyz"), ["XYZ"]);
  assert.throws(() => airportList("AMS ams"), /must not repeat/);
  assert.throws(() => airportList("AMS FRA1"), /Invalid airport code: FRA1/);
  assert.throws(() => airportList("AAA BBB CCC DDD EEE FFF GGG"), /at most six/);
});

test("via suggestions replace the active token and retain the other airports", () => {
  assert.deepEqual(airportListSuggestions("LHR, A", 6, ["LHR", "AMS", "CDG"]), [
    { code: "AMS", value: "LHR, AMS", caret: 8 },
  ]);
  assert.deepEqual(
    airportListSuggestions("LHR, , CDG", 5, ["LHR", "AMS", "KEF", "CDG"]),
    [
      { code: "AMS", value: "LHR, AMS, CDG", caret: 8 },
      { code: "KEF", value: "LHR, KEF, CDG", caret: 8 },
    ],
  );
  assert.deepEqual(airportListSuggestions("LHR ", 4, ["LHR", "AMS"]), [
    { code: "AMS", value: "LHR AMS", caret: 7 },
  ]);
  assert.deepEqual(airportListSuggestions("LHR, XYZ", 8, ["LHR", "AMS"]), []);
});
function transport(t) {
  const requests = new Map();
  t.mock.method(globalThis, "fetch", (url, options) => {
    const pending = deferred();
    requests.set(url, { ...pending, options });
    return pending.promise;
  });
  return requests;
}
const response = (payload, ok = true) => ({ ok, json: async () => payload });

for (const staleFails of [false, true]) {
  test(`only the latest response updates state (stale failure: ${staleFails})`, async (t) => {
    const requests = transport(t),
      lane = new RequestLane(),
      seen = [],
      failures = [];
    let finished = 0;
    const run = (url) =>
      lane.run(
        url,
        {},
        (data) => seen.push(data),
        (error) => failures.push(error),
        () => finished++,
      );
    const slow = run("slow"),
      fast = run("fast");
    assert.equal(requests.get("slow").options.signal.aborted, true);
    requests.get("fast").resolve(response("latest"));
    await fast;
    if (staleFails) requests.get("slow").reject(Error("stale"));
    else requests.get("slow").resolve(response("stale"));
    await slow;
    assert.deepEqual(seen, ["latest"]);
    assert.deepEqual(failures, []);
    assert.equal(finished, 1);
  });
}

test("cancelling during JSON decoding suppresses callbacks", async (t) => {
  const requests = transport(t),
    decoded = deferred(),
    lane = new RequestLane();
  const seen = [];
  const running = lane.run(
    "query",
    undefined,
    () => seen.push("received"),
    () => seen.push("failed"),
    () => seen.push("finished"),
  );
  requests.get("query").resolve({ ok: true, json: () => decoded.promise });
  await Promise.resolve();
  lane.cancel();
  decoded.resolve({ items: [] });
  await running;
  assert.deepEqual(seen, []);
  assert.equal(requests.get("query").options.signal.aborted, true);
  assert.equal(lane.controller, null);
});

test("API errors are delivered once and release the request lane", async (t) => {
  const requests = transport(t),
    lane = new RequestLane(),
    seen = [];
  let finished = 0;
  const error = { detail: { code: "storage_unavailable" } };
  const running = lane.run(
    "query",
    undefined,
    () => assert.fail("received failed response"),
    (data) => seen.push(data),
    () => finished++,
  );
  requests.get("query").resolve(response(error, false));
  await running;
  assert.deepEqual(seen, [error]);
  assert.equal(finished, 1);
  assert.equal(lane.controller, null);
});

test("untrusted flight metadata is escaped before HTML insertion", () => {
  assert.equal(
    escapeHTML(`<img src="x" onerror='alert(1)'>&`),
    "&lt;img src=&quot;x&quot; onerror=&#39;alert(1)&#39;&gt;&amp;",
  );
});

test("airport-local dates and inputs follow the specified zone across DST", () => {
  assert.equal(
    localDate("2026-10-18T02:00:00Z", "America/Toronto"),
    "2026-10-17",
  );
  assert.equal(localDate("2026-10-18T02:00:00Z", "Asia/Tokyo"), "2026-10-18");
  assert.equal(
    localInput("2026-11-01T05:30:00Z", "America/Toronto"),
    "2026-11-01T01:30",
  );
  assert.equal(
    localInput("2026-11-01T06:30:00Z", "America/Toronto"),
    "2026-11-01T01:30",
  );
});


import { qualityLabels, qualityBadges } from "../src/server/web/quality.mjs";

test("quality badges prioritize conflicts, deduplicate labels and escape metadata", () => {
  const flight = { quality: { labels: [
    { code: "gap", severity: "caution", label: "Observation gap", detail: "A gap" },
    { code: "timing", severity: "warning", label: "Timing conflict", detail: '<script>"' },
    { code: "bad", severity: '\" onclick=\"bad', label: "Unsafe" },
  ] } };
  assert.deepEqual(qualityLabels([flight, flight]).map((x) => x.code), ["timing", "gap"]);
  assert.ok(qualityBadges([flight]).includes("&lt;script&gt;&quot;"));
  assert.ok(!qualityBadges([flight]).includes("onclick"));
});

test("a partly confirmed journey does not claim all routes match VRS", () => {
  const confirmed = { route_verified: true, quality: { labels: [ {code: "vrs_route_match", severity: "info", label: "VRS route match", detail: "Undated"} ] } };
  assert.equal(qualityLabels([confirmed, {}]).length, 0);
  assert.equal(qualityLabels([confirmed]).length, 1);
});


test("flight identity retains callsign and shows operator reference name", () => {
  assert.equal(flightLabel({ callsign: "ACA541", operator_name: "Air Canada" }), "ACA541 (callsign) · Air Canada (operator)");
  assert.equal(flightLabel({ callsign: "N5203D" }), "N5203D (callsign)");
  assert.equal(escapeHTML(flightLabel({ callsign: "ACA541", operator_name: "<script>" })).includes("<script>"), false);
});
