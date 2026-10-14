import assert from "node:assert/strict";
import test from "node:test";
import {
  escapeHTML,
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
