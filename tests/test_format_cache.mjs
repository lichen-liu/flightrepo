import assert from "node:assert/strict";
import test from "node:test";

// Each import has an independent cache so constructor counts describe this case.
const fresh = (name) => import(`../src/server/web/format.mjs?cache-test=${name}`);
const instant = "2026-11-01T06:30:00Z";
const stampOptions = (timeZone) => ({
  timeZone,
  year: "numeric",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZoneName: "shortOffset",
});

function formatterCounter(t) {
  const native = Intl.DateTimeFormat;
  let constructions = 0;
  t.mock.method(Intl, "DateTimeFormat", function (locale, options) {
    constructions++;
    return new native(locale, options);
  });
  return () => constructions;
}

test("repeated cards reuse formatters while date, clock and timezone shapes stay separate", async (t) => {
  const count = formatterCounter(t),
    { stamp, localDate, localInput } = await fresh("reuse");
  for (let i = 0; i < 100; i++) {
    stamp(instant, "America/Toronto");
    localDate(instant, "America/Toronto");
    localInput(instant, "America/Toronto");
    stamp(instant, "Europe/Paris");
  }
  assert.equal(count(), 4);
});

test("formatter reuse preserves local dates, repeated DST clocks and half-hour transitions", async () => {
  const { stamp, localDate, localInput } = await fresh("dst");
  const cases = [
    ["2026-11-01T05:30:00Z", "America/Toronto", "2026-11-01T01:30"],
    ["2026-11-01T06:30:00Z", "America/Toronto", "2026-11-01T01:30"],
    ["2026-04-04T14:45:00Z", "Australia/Lord_Howe", "2026-04-05T01:45"],
    ["2026-04-04T15:15:00Z", "Australia/Lord_Howe", "2026-04-05T01:45"],
    ["2026-10-17T23:30:00Z", "Asia/Kathmandu", "2026-10-18T05:15"],
  ];
  for (let repeat = 0; repeat < 3; repeat++)
    for (const [value, zone, local] of cases) {
      assert.equal(localInput(value, zone), local);
      assert.equal(localDate(value, zone), local.slice(0, 10));
      assert.equal(stamp(value, zone), new Intl.DateTimeFormat("en-GB", stampOptions(zone)).format(new Date(value)));
    }
  assert.notEqual(stamp(cases[0][0], cases[0][1]), stamp(cases[1][0], cases[1][1]));
});

test("exploring many zones evicts the least recently used formatter instead of growing forever", async (t) => {
  const count = formatterCounter(t),
    { stamp } = await fresh("bound"),
    zones = Intl.supportedValuesOf("timeZone").slice(0, 49);
  assert.equal(zones.length, 49);
  for (const zone of zones.slice(0, 48)) stamp(instant, zone);
  assert.equal(count(), 48);
  stamp(instant, zones[0]); // Recently used formatters survive the next insertion.
  stamp(instant, zones[48]);
  assert.equal(count(), 49);
  stamp(instant, zones[0]);
  assert.equal(count(), 49);
  stamp(instant, zones[1]);
  assert.equal(count(), 50);
});

test("invalid zones and invalid instants retain Intl errors, and zone coercion is not cached", async () => {
  const { stamp, localDate, localInput } = await fresh("errors");
  for (let repeat = 0; repeat < 2; repeat++)
    for (const format of [stamp, localDate, localInput]) {
      assert.throws(() => format(instant, "Invalid/Timezone"), RangeError);
      assert.throws(() => format("not-a-date", "UTC"), RangeError);
    }
  let zone = "UTC";
  const dynamicZone = { toString: () => zone };
  assert.equal(stamp(instant, dynamicZone), stamp(instant, "UTC"));
  zone = "America/Toronto";
  assert.equal(stamp(instant, dynamicZone), stamp(instant, "America/Toronto"));
  zone = "Invalid/Timezone";
  assert.throws(() => stamp(instant, dynamicZone), RangeError);
});

test("structured display parts tolerate browser-specific date separators and reuse the stamp formatter", async (t) => {
  const native = Intl.DateTimeFormat;
  let constructions = 0;
  t.mock.method(Intl, "DateTimeFormat", function (locale, options) {
    constructions++;
    const formatter = new native(locale, options);
    return {
      format: value => formatter.format(value).replace(", ", " at "),
      formatToParts: value => formatter.formatToParts(value).map(part =>
        part.type === "literal" ? {...part,value:" at "} : part),
    };
  });
  const { stamp, stampParts } = await fresh("browser-separators");
  const value = "2026-10-17T18:00:00Z";
  assert.ok(stamp(value, "America/Toronto").includes(" at "));
  assert.deepEqual(stampParts(value, "America/Toronto"), {
    date: "17 Oct 2026", clock: "14:00", offset: "GMT-4",
  });
  assert.equal(constructions, 1);
});
