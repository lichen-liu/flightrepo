import assert from "node:assert/strict";
import test from "node:test";
import { calendarDays } from "../src/server/web/calendar.mjs";
import { RouteMap, drawTimeline } from "../src/server/web/map.mjs";
import d3 from "../src/server/web/vendor/d3-7.9.0.min.js";
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

test("map batches rapid interaction and cursor updates into one frame with the latest state", (t) => {
  const frames = [],
    rendered = [];
  const previousFrame = Object.getOwnPropertyDescriptor(globalThis, "requestAnimationFrame");
  t.after(() => {
    if (previousFrame) Object.defineProperty(globalThis, "requestAnimationFrame", previousFrame);
    else delete globalThis.requestAnimationFrame;
  });
  Object.defineProperty(globalThis, "requestAnimationFrame", {
    configurable: true,
    value: (callback) => {
      frames.push(callback);
      return frames.length;
    },
  });
  const map = Object.create(RouteMap.prototype);
  map.draw = () => rendered.push([map.rotation[0], map.cursor]);
  map.rotation = [0, 0, 0];
  for (let i = 0; i < 100; i++) {
    map.rotate(1);
    map.setCursor(i);
  }
  assert.equal(frames.length, 1);
  assert.deepEqual(rendered, []);
  frames[0]();
  assert.deepEqual(rendered, [[100, 99]]);
  map.rotate(25);
  assert.equal(frames.length, 2);
  frames[1]();
  assert.deepEqual(rendered, [[100, 99], [125, 99]]);
});

// A small SVG selection adapter retains nodes so these checks can verify cursor
// updates without a browser. Real D3 supplies the UTC scales and tick values.
function timelineSurface(t) {
  const makeNode = (tag, parent = null) => ({
    tag, parent, children: [], attributes: {},
    get firstElementChild() { return this.children[0] || null; },
  });
  const container = makeNode("div");
  container.clientWidth = 500;
  container.replaceChildren = () => { container.children = []; };
  let axes = 0;
  function selection(nodes, parents = []) {
    return {
      append(tag) {
        return selection(nodes.map(parent => {
          const child = makeNode(tag, parent);
          child.datum = parent.datum;
          parent.children.push(child);
          return child;
        }));
      },
      attr(name, value) {
        nodes.forEach(node => { node.attributes[name] = typeof value === "function" ? value(node.datum) : value; });
        return this;
      },
      text(value) {
        nodes.forEach(node => { node.text = typeof value === "function" ? value(node.datum) : value; });
        return this;
      },
      selectAll(selector) {
        const found = [];
        const visit = node => node.children.forEach(child => {
          if (child.attributes.class?.split(" ").includes(selector.slice(1))) found.push(child);
          visit(child);
        });
        nodes.forEach(visit);
        return selection(found, nodes);
      },
      data(values) { this.values = values; return this; },
      join(tag) {
        const parent = parents[0], values = this.values;
        const joined = values.map((value, index) => {
          const node = nodes[index] || makeNode(tag, parent);
          if (!nodes[index]) parent.children.push(node);
          node.datum = value;
          return node;
        });
        nodes.slice(values.length).forEach(node => { node.parent.children = node.parent.children.filter(child => child !== node); });
        return selection(joined);
      },
      call(callback) { callback(this); return this; },
      node() { return nodes[0]; },
    };
  }
  const axisBottom = () => {
    const axis = () => { axes++; };
    for (const name of ["tickValues", "tickFormat", "tickSize"]) axis[name] = () => axis;
    return axis;
  };
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { d3: { ...d3, select: node => selection([node]), axisBottom } } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else delete globalThis.window;
  });
  return { container, axes: () => axes };
}
const timelineFlights = () => [
  { departure_scheduled: "2026-10-17T08:00:00Z", arrival_scheduled: "2026-10-17T10:00:00Z" },
  { departure_scheduled: "2026-10-17T11:00:00Z", arrival_scheduled: "2026-10-17T14:00:00Z" },
];

test("timeline scrubbing retains flight bars and ticks while moving one cursor", t => {
  const { container, axes } = timelineSurface(t), legs = timelineFlights();
  drawTimeline(container, legs, Date.parse("2026-10-17T08:00:00Z"));
  const svg = container.firstElementChild, staticNodes = svg.children.filter(node => node.attributes.class !== "timeline-cursor");
  for (let index = 0; index < 200; index++)
    drawTimeline(container, legs.map(flight => ({...flight})), Date.parse("2026-10-17T11:00:00Z"));
  assert.equal(axes(), 1);
  assert.equal(container.firstElementChild, svg);
  assert.deepEqual(svg.children.filter(node => node.attributes.class !== "timeline-cursor"), staticNodes);
  const cursors = svg.children.filter(node => node.attributes.class === "timeline-cursor");
  assert.equal(cursors.length, 1);
  assert.equal(cursors[0].attributes.x1, (500 - 36) / 2);
  drawTimeline(container, legs, null);
  assert.equal(svg.children.some(node => node.attributes.class === "timeline-cursor"), false);
  assert.equal(axes(), 1);
});

test("timeline rebuilds for resize and edited times and recovers from cleared or hidden content", t => {
  const { container, axes } = timelineSurface(t), legs = timelineFlights();
  drawTimeline(container, legs, 0);
  const first = container.firstElementChild;
  container.clientWidth = 700;
  drawTimeline(container, legs, 0);
  assert.notEqual(container.firstElementChild, first);
  assert.equal(container.firstElementChild.attributes.viewBox, "0 0 664 79");
  legs[1].arrival_scheduled = "2026-10-17T15:00:00Z";
  drawTimeline(container, legs, Date.parse("2026-10-17T15:00:00Z"));
  assert.equal(axes(), 3);
  assert.match(container.firstElementChild.attributes["aria-label"], /15:00/);
  container.replaceChildren();
  drawTimeline(container, legs, 0);
  assert.equal(axes(), 4);
  container.clientWidth = 0;
  drawTimeline(container, legs, 0);
  assert.equal(container.firstElementChild, null);
  container.clientWidth = 500;
  drawTimeline(container, legs, "invalid");
  assert.equal(axes(), 5);
  assert.equal(container.firstElementChild.children.some(node => node.attributes.class === "timeline-cursor"), false);
  drawTimeline(container, [], null);
  assert.equal(container.firstElementChild, null);
});

test("calendar disables gaps and handles leap years without using the browser timezone", () => {
  const available = new Set(["2024-02-01", "2024-02-29"]),
    days = calendarDays("2024-02", available);
  assert.equal(days.length, 29);
  assert.deepEqual(
    days.filter((day) => day.enabled).map((day) => day.date),
    [...available],
  );
  assert.equal(calendarDays("2025-02", new Set()).length, 28);
  assert.equal(days[15].enabled, false);
});

test("normal scrolling passes through the globe; modified scrolling zooms it", () => {
  const map = Object.create(RouteMap.prototype);
  map.mode = "globe";
  map.zoom = 1;
  map.requestDraw = () => {};
  let prevented = 0;
  const event = { deltaY: -100, preventDefault: () => prevented++ };
  map.onWheel(event);
  assert.equal(map.zoom, 1);
  assert.equal(prevented, 0);
  map.onWheel({ ...event, ctrlKey: true });
  assert.ok(map.zoom > 1);
  map.onWheel({ ...event, metaKey: true });
  assert.equal(prevented, 2);
  map.mode = "flat";
  const previous = map.zoom;
  map.onWheel({ ...event, ctrlKey: true });
  assert.equal(map.zoom, previous);
  assert.equal(prevented, 2);
});

test("via lists preserve travel order across whitespace, commas and trailing separators", () => {
  assert.deepEqual(airportList(" ams,  lhr ,,\n"), ["AMS", "LHR"]);
  assert.deepEqual(airportList("lhr ams"), ["LHR", "AMS"]);
  assert.deepEqual(airportList(""), []);
  assert.deepEqual(airportList("xyz"), ["XYZ"]);
  assert.throws(() => airportList("AMS ams"), /must not repeat/);
  assert.throws(() => airportList("AMS FRA1"), /Invalid airport code: FRA1/);
  assert.equal(airportList("AAA BBB CCC DDD EEE FFF GGG").length, 7);
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
  const flight = {
    quality: {
      labels: [
        {
          code: "gap",
          severity: "caution",
          label: "Observation gap",
          detail: "A gap",
        },
        {
          code: "timing",
          severity: "warning",
          label: "Timing conflict",
          detail: '<script>"',
        },
        { code: "bad", severity: '\" onclick=\"bad', label: "Unsafe" },
      ],
    },
  };
  assert.deepEqual(
    qualityLabels([flight, flight]).map((x) => x.code),
    ["timing", "gap"],
  );
  assert.ok(qualityBadges([flight]).includes("&lt;script&gt;&quot;"));
  assert.ok(!qualityBadges([flight]).includes("onclick"));
});

test("informational confidence notes are confined to leg details", () => {
  const noted = {
    quality: {
      labels: [
        {
          code: "approximate_times",
          severity: "info",
          label: "Approximate times",
          detail: "Times are ranges",
        },
      ],
    },
  };
  assert.equal(qualityLabels([noted]).length, 0);
  assert.equal(qualityLabels([noted], { details: true }).length, 1);
});

test("flight identity retains callsign and shows operator reference name", () => {
  assert.equal(
    flightLabel({ callsign: "ACA541", operator_name: "Air Canada" }),
    "ACA541 (callsign) · Air Canada (operator)",
  );
  assert.equal(flightLabel({ callsign: "N5203D" }), "N5203D (callsign)");
  assert.equal(
    escapeHTML(
      flightLabel({ callsign: "ACA541", operator_name: "<script>" }),
    ).includes("<script>"),
    false,
  );
});

test("request activity clears immediately on cancellation without hiding a newer request", async (t) => {
  const requests = transport(t),
    active = new Set(),
    stopped = [];
  const lane = new RequestLane((url) => {
    active.add(url);
    return () => {
      active.delete(url);
      stopped.push(url);
    };
  });
  const first = lane.run(
    "/first",
    undefined,
    () => {},
    () => {},
  );
  assert.deepEqual([...active], ["/first"]);
  const second = lane.run(
    "/second",
    undefined,
    () => {},
    () => {},
  );
  assert.deepEqual([...active], ["/second"]);
  requests.get("/first").resolve({ ok: true, json: async () => ({}) });
  await first;
  assert.deepEqual([...active], ["/second"]);
  requests.get("/second").resolve({ ok: true, json: async () => ({}) });
  await second;
  assert.equal(active.size, 0);
  assert.deepEqual(stopped, ["/first", "/second"]);
});
