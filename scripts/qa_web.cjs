// Behavior checks against the real demo API. No saved output or screenshot baselines.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { test, before, after } = require("node:test");
const { chromium, webkit } = require("playwright");
const { expect } = require("playwright/test");
const base = process.env.FLYJI_QA_URL || "http://127.0.0.1:8013";
const demoSearch = `${base}?origin=YYZ&destination=CDG&date=2026-10-17&time-basis=scheduled&input-clock=local`;
const out = process.env.FLYJI_QA_OUTPUT || path.resolve("var/web-qa");
const searchPath = /\/v1\/itineraries\/search(?:\/local)?$/;
const identity = (legs) =>
  legs.map(({ flight_id }) => ({ flight_id }));
let browser;
before(async () => {
  browser = await (process.env.FLYJI_QA_BROWSER === "webkit" ? webkit : chromium).launch({
    headless: true,
    ...(process.env.FLYJI_QA_BROWSER === "chrome" ? { channel: "chrome" } : {}),
  });
});
after(async () => {
  await browser?.close();
});

function check(name, run, options = {}) {
  test(name, { timeout: 30_000 }, async () => {
    const context = await browser.newContext({
      viewport: { width: 1180, height: 1000 },
      timezoneId: "Asia/Tokyo",
      reducedMotion: "reduce",
      ...options,
    });
    const errors = [],
      requests = [];
    context.on("page", (page) => {
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (
          message.type() === "error" &&
          !/Failed to load resource.*(?:4\d\d|5\d\d)/.test(message.text())
        )
          errors.push(message.text());
      });
    });
    context.on("request", (request) => requests.push(request.url()));
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    try {
      await run(page, context);
      assert.deepEqual(errors, [], "no JavaScript or CSP errors");
      assert.ok(
        requests.every((url) => new URL(url).origin === new URL(base).origin),
        "runtime requests stay on the app origin",
      );
    } catch (error) {
      await fs.mkdir(out, { recursive: true });
      await page
        .screenshot({
          path: path.join(out, name.replace(/[^a-z0-9]+/gi, "-") + ".png"),
          fullPage: true,
        })
        .catch(() => {});
      throw error;
    } finally {
      await context.close();
    }
  });
}
async function result(page, endpoint, action) {
  const pending = page.waitForResponse(
    (response) =>
      endpoint.test(new URL(response.url()).pathname) &&
      response.request().method() === "POST",
  );
  // An action failure closes the context; retain that original failure instead
  // of reporting an unrelated unhandled wait rejection during cleanup.
  pending.catch(() => {});
  await action();
  const response = await pending;
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}
async function open(page, routes = true) {
  const data = await result(page, searchPath, () => page.goto(demoSearch));
  await expect(
    page.getByRole("button", { name: "Search flights", exact: true }),
  ).toBeEnabled();
  if (routes) await expect(page.locator("[data-option]").first()).toBeVisible();
  assert.ok(Number.isFinite(data.search_time_seconds));
  await expect(page.locator("#search-time")).toHaveText(`Search time: ${data.search_time_seconds.toFixed(2)} s`);
  return data;
}
async function search(
  page,
  action = () =>
    page.getByRole("button", { name: "Search flights", exact: true }).click(),
) {
  const data = await result(page, searchPath, action);
  await expect(
    page.getByRole("button", { name: "Search flights", exact: true }),
  ).toBeEnabled();
  assert.ok(Number.isFinite(data.search_time_seconds));
  await expect(page.locator("#search-time")).toHaveText(`Search time: ${data.search_time_seconds.toFixed(2)} s`);
  return data;
}
// The main flow stays compact; tests explicitly reveal specialist controls.
async function openSearchOptions(page) {
  await page.locator("#search-details").evaluate((details) => { details.open = true; });
}
async function openDataSettings(page) {
  await openSearchOptions(page);
  await page.locator("#data-time-settings").evaluate((details) => { details.open = true; });
}
async function openMapControls(page) {
  await page.locator(".map-toolbar").evaluate((details) => { details.open = true; });
}
async function openJourneyTimeline(page) {
  await page.locator(".schedule-panel").evaluate((details) => { details.open = true; });
}
async function revealControl(control) {
  await control.evaluate((element) => {
    for (let parent = element.parentElement; parent; parent = parent.parentElement)
      if (parent.tagName === "DETAILS") parent.open = true;
  });
}
async function chooseDate(page, name, date) {
  const input = page.locator(`input[name="${name}"]`);
  await revealControl(input);
  await expect(input).toBeEnabled();
  await input.click();
  await page.locator(`#${name}-calendar button[data-date="${date}"]`).click();
}

async function fulfillAvailability(route, data) {
  const params = new URL(route.request().url()).searchParams,
    dates = data.dates || [], requested = params.get("departure_date");
  await route.fulfill({ json: {
    origin: params.get("origin"), destination: params.get("destination"),
    time_basis: params.get("time_basis"), timezone: "UTC",
    selected_date: dates.includes(requested) ? requested : dates[0] || requested,
    outbound: dates.length ? 1 : 0, inbound: dates.length ? 5 : 0,
    ...data,
  } });
}

check("flight details show the flight's stored aircraft type code", async (page) => {
  await page.route("**/v1/itineraries/search/local", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.items[0].legs[0].aircraft_type_code = "B738";
    await route.fulfill({ response, json: data });
  });
  await open(page);
  await expect(page.locator(".detail-evidence").first()).not.toHaveAttribute("open", "");
  await page.locator(".detail-evidence summary").first().click();
  await expect(page.locator("#legs")).toContainText("Aircraft type: B738");
  await expect(page.getByText("Aircraft type: B738", { exact: true })).toBeVisible();
});

check("observed route cards retain uncertainty and fit narrow phones", async (page) => {
  const shift = (instant, minutes) => new Date(Date.parse(instant) + minutes * 60000).toISOString();
  await page.route("**/v1/itineraries/search/local", async route => {
    const response = await route.fetch(), data = await response.json();
    for (const item of data.items) {
      item.legs = item.legs.map(flight => ({...flight,time_basis:"observed",
        departure_lower:shift(flight.departure_scheduled,-5),departure_upper:shift(flight.departure_scheduled,5),
        arrival_lower:shift(flight.arrival_scheduled,-5),arrival_upper:shift(flight.arrival_scheduled,5)}));
      item.departure = item.legs[0].departure_lower;
      item.arrival = item.legs.at(-1).arrival_upper;
      item.duration_minutes = (Date.parse(item.arrival) - Date.parse(item.departure)) / 60000;
      item.connections.forEach((connection, i) => {
        connection.wait_minutes = (Date.parse(item.legs[i+1].departure_lower) - Date.parse(item.legs[i].arrival_upper)) / 60000;
      });
    }
    await route.fulfill({response,json:data});
  });
  await open(page);
  await expect(page.locator(".option-endpoint time").first()).toContainText("≈");
  await expect(page.locator(".option-badges").first()).toContainText("Observed range");
  await expect(page.locator(".option-duration strong").first()).toContainText("–");
  await expect(page.locator(".option-timeline").first()).toHaveAttribute("aria-label", /Observed time bounds/);
  await expect(page.locator(".detail-stop-duration").first()).toContainText("At least");
  await page.setViewportSize({width:320,height:740});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const bounds = await page.locator(".option-schedule").first().evaluate(element =>
    [...element.children].map(child => ({left:child.getBoundingClientRect().left,right:child.getBoundingClientRect().right})));
  assert.ok(bounds[0].right <= bounds[1].left && bounds[1].right <= bounds[2].left, "time columns do not overlap");
});

check("sorting keeps selection camera and cursor without applying search drafts", async (page) => {
  const initial = await open(page);
  await page.locator("[data-option]").nth(1).click();
  const selected = identity((await exported(page)).selection.legs);
  await page.locator("#globe-mode").click();
  await expect(page.locator("#route-map")).toHaveAttribute("data-mode", "globe");
  await openMapControls(page);
  const land = page.locator("#route-map .map-land");
  const fitted = await land.getAttribute("d");
  await page.locator("#rotate-left").click();
  await expect(land).not.toHaveAttribute("d", fitted);
  const camera = await land.getAttribute("d");
  await openJourneyTimeline(page);
  await page.locator("#time-cursor").fill("500");
  await page.getByLabel("Maximum connection (days)").fill("0.5");
  await expect(page.locator("#draft-notice")).toBeVisible();
  let submitted;
  page.on("request", request => {
    if (searchPath.test(new URL(request.url()).pathname)) submitted = request.postDataJSON();
  });
  const sorted = await result(page, searchPath, () => page.locator('[name="ranking"]').selectOption("shortest_elapsed"));
  await expect(page.locator("#status")).toContainText("selected journey is unchanged");
  assert.equal(sorted.query.ranking, "shortest_elapsed");
  assert.equal(submitted.data_revision, initial.data_revision);
  assert.equal(submitted.offset, 0);
  assert.equal(submitted.maximum_connection_minutes, 10080);
  assert.equal(new URL(page.url()).searchParams.get("maximum"), "10080");
  assert.deepEqual(identity((await exported(page)).selection.legs), selected);
  await expect(page.getByLabel("Maximum connection (days)")).toHaveValue("0.5");
  await expect(page.locator("#draft-notice")).toBeVisible();
  await expect(page.locator("#globe-mode")).toHaveAttribute("aria-pressed", "true");
  await expect.poll(async () => await land.getAttribute("d") === camera).toBe(true);
  await expect(page.locator("#time-cursor")).toHaveValue("500");
});

check("sorting and pagination lock their shared controls and recover after failure", async (page) => {
  let hold = false, release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route("**/v1/itineraries/search/local", async route => {
    if (hold) await gate;
    await route.continue({postData:JSON.stringify({...route.request().postDataJSON(),limit:2})});
  });
  await open(page);
  await expect(page.locator("#more-routes")).toBeVisible();
  hold = true;
  const sorted = result(page, searchPath, () => page.locator('[name="ranking"]').selectOption("shortest_elapsed"));
  await expect(page.locator("#more-routes")).toBeDisabled();
  await expect(page.locator('[name="ranking"]')).toBeDisabled();
  await expect(page.locator("#search-button")).toBeDisabled();
  release();
  await sorted;
  await expect(page.locator("#more-routes")).toBeEnabled();
  await expect(page.locator('[name="ranking"]')).toBeEnabled();
  await expect(page.locator("#search-button")).toBeEnabled();
  await page.route("**/v1/itineraries/search/local", route => route.fulfill({status:503,json:{detail:{message:"Temporarily unavailable"}}}));
  await page.locator('[name="ranking"]').selectOption("fewest_legs");
  await expect(page.locator("#status")).toContainText("Could not reorder");
  await expect(page.locator("#more-routes")).toBeEnabled();
  await expect(page.locator('[name="ranking"]')).toHaveValue("shortest_elapsed");
  await expect(page.locator("#search-button")).toBeEnabled();
});

check("journey pages append results and preserve the selected journey", async (page) => {
  const requests = [];
  await page.route("**/v1/itineraries/search/local", async (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    await route.continue({ postData: JSON.stringify({ ...body, limit: 2 }) });
  });
  const first = await open(page);
  assert.equal(first.items.length, 2);
  const selected = await page.locator('[data-option][aria-pressed="true"]').innerText();
  await expect(page.locator("#more-routes")).toBeVisible();
  const nextPage = await result(page, searchPath, () => page.locator("#more-routes").click());
  await expect(page.locator("[data-option]")).toHaveCount(4);
  await expect(page.locator("#search-time")).toHaveText(`Search time: ${nextPage.search_time_seconds.toFixed(2)} s · latest page`);
  assert.equal(requests.at(-1).offset, 2);
  assert.equal(requests.at(-1).data_revision, first.data_revision);
  assert.equal(await page.locator('[data-option][aria-pressed="true"]').innerText(), selected);
  assert.equal(new Set(await page.locator("[data-option]").allTextContents()).size, 4);
  await page.locator("[data-option]").nth(3).click();
  const laterSelection = (await exported(page)).selection;
  const sorted = await result(page, searchPath, () => page.locator('[name="ranking"]').selectOption("shortest_elapsed"));
  assert.ok(!sorted.items.some(item => item.itinerary_id === laterSelection.itinerary_id));
  assert.deepEqual(identity((await exported(page)).selection.legs), identity(laterSelection.legs));
  await expect(page.locator("#journey-status")).toContainText("Arrives at CDG");
});

check("arrival window is optional and survives shared search links", async (page) => {
  const initial = await open(page);
  assert.equal(initial.query.maximum_connection_minutes, 10080);
  await openSearchOptions(page);
  await expect(page.getByLabel("Maximum connection (days)")).toHaveValue("7");
  await expect(page.locator('[name="onward-days"]')).toHaveValue("");
  await page.locator('[name="onward-days"]').fill("100");
  await page.locator('[name="onward-days"]').press("Tab");
  await expect(page.locator("#date-zones")).toContainText("100 days");
  const data = await search(page);
  assert.equal(Date.parse(data.query.horizon_end) - Date.parse(data.query.departure_end), 100 * 86400000);
  assert.equal(new URL(page.url()).searchParams.get("onward-days"), "100");
});

check("More options applies preferences and closes without discarding them", async (page) => {
  await open(page);
  const preferences = page.locator("#search-details"),
    summary = page.locator("#search-details > summary"),
    via = page.getByLabel("Via airports", { exact: true });
  assert.equal(await preferences.evaluate((details) => details.open), false);
  await summary.click();
  await via.fill("LHR");
  const data = await result(page, searchPath, () =>
    page.getByRole("button", { name: "Apply & search", exact: true }).click(),
  );
  assert.deepEqual(data.query.via_airport, ["LHR"]);
  assert.equal(await preferences.evaluate((details) => details.open), false);
  await summary.click();
  await expect(via).toHaveValue("LHR");
  await page.locator("#page-title").click();
  assert.equal(await preferences.evaluate((details) => details.open), false);
  await summary.click();
  await via.press("Escape");
  assert.equal(await preferences.evaluate((details) => details.open), false);
  await summary.click();
  await expect(via).toHaveValue("LHR");
});

check("custom connection days reach search and survive shared links", async (page) => {
  await open(page);
  const maximum = page.getByLabel("Maximum connection (days)");
  await expect(maximum).toBeVisible();
  await expect(maximum).toHaveValue("7");
  for (const [days, minutes] of [["10", 14400], ["0.5", 720], ["", null]]) {
    await maximum.fill(days);
    const data = await search(page);
    assert.equal(data.query.maximum_connection_minutes, minutes);
    assert.equal(new URL(page.url()).searchParams.get("maximum"), String(minutes ?? "any"));
    const restored = await result(page, searchPath, () => page.reload());
    assert.equal(restored.query.maximum_connection_minutes, minutes);
      await expect(maximum).toHaveValue(days);
  }
  await result(page, searchPath, () => page.goto(demoSearch + "&maximum=360"));
  await expect(maximum).toHaveValue("0.25");
});

check("stop count defaults to unbounded and survives links exports and connection editing", async (page) => {
  const initial = await open(page);
  assert.equal(initial.query.max_legs, null);
  await expect(page.getByLabel("Maximum stops")).toHaveValue("any");
  assert.equal(new URL(page.url()).searchParams.get("stops"), "any");
  assert.equal((await exported(page)).query.max_legs, null);
  await edit(page);
  const pending = page.waitForRequest((request) => new URL(request.url()).pathname === "/v1/plans/rebuild");
  await next(page);
  assert.equal((await pending).postDataJSON().max_legs, null);
  await result(page, searchPath, () => page.goto(`${demoSearch}&stops=5`));
  await expect(page.getByLabel("Maximum stops")).toHaveValue("5");
  const finite = await search(page);
  assert.equal(finite.query.max_legs, 6);
  await openSearchOptions(page);
  await page.getByLabel("Maximum stops").selectOption("any");
  const unlimited = await search(page);
  assert.equal(unlimited.query.max_legs, null);
});

check("accuracy warnings remain visible without disclosing audit evidence", async (page) => {
  await page.route("**/v1/itineraries/search/local", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.items[0].legs[0].quality = { level: "uncertain", labels: [
      { code: "uncertain_itinerary", severity: "caution", label: "Uncertain itinerary", detail: "Some itinerary information is uncertain." },
    ] };
    await route.fulfill({ response, json: data });
  });
  await open(page);
  await expect(page.locator("[data-option]").first()).toContainText("Uncertain itinerary");
  await expect(page.locator("#legs")).toContainText("Uncertain itinerary");
});

check("public responses exports and page keep provenance inside the backend", async (page) => {
  const data = await open(page);
  const exportedData = await exported(page);
  const forbidden = /adsblol|adsb\.lol|ourairports|vrs|provider_id|route_verification|reference_sha256|review_reasons|evidence_kind|snapshot_id/;
  assert.doesNotMatch(JSON.stringify(data), forbidden);
  assert.doesNotMatch(JSON.stringify(exportedData), forbidden);
  assert.doesNotMatch(await page.locator("body").innerText(), forbidden);
  const notice = page.getByRole("link", {name: "Data sources & licences"});
  await expect(notice).toBeVisible();
  assert.equal(new URL(exportedData.data_licence_notice).pathname, "/data-sources");
  await notice.click();
  await expect(page.getByRole("heading", {name: "Data sources & licences"})).toBeVisible();
});

check("uncertain and conflicting flight evidence has consistent visible labels", async (page) => {
  await page.route("**/v1/itineraries/search/local", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.items[0].legs[0].quality = {level:"conflict",labels:[
      {code:"observation_gap",severity:"caution",label:"Observation gap",detail:"An intermediate stop may be unobserved."},
      {code:"timing_conflict",severity:"warning",label:"Timing conflict",detail:"Airports and time window imply an implausible speed."},
    ]};
    await route.fulfill({response,json:data});
  });
  await open(page);
  await expect(page.locator("[data-option]").first()).toContainText("Timing conflict");
  await expect(page.locator("[data-option]").first()).toContainText("Observation gap");
  await expect(page.locator("#legs .quality-warning")).toContainText("implausible speed");
});

check("calendar disables gaps, repairs unavailable links, and handles origins with no data", async (page) => {
  await page.route("**/v1/search-availability?*", async (route) => {
    const origin = new URL(route.request().url()).searchParams.get("origin");
    await fulfillAvailability(route, { origin, timezone: "America/Toronto", dates: origin === "YYZ" ? ["2026-10-17", "2026-10-19"] : [] });
  });
  await result(page, searchPath, () => page.goto(demoSearch.replace("2026-10-17", "2025-01-01")));
  await expect(page.locator("#date")).toHaveValue("2026-10-17");
  await page.locator("#date").click();
  await expect(page.locator('#date-calendar [data-date="2026-10-18"]')).toBeDisabled();
  await expect(page.locator('#date-calendar [data-date="2026-10-19"]')).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.locator("#date-calendar")).toBeHidden();
  await expect(page.locator("#date")).toBeFocused();
  await page.getByLabel("From", { exact: true }).fill("XYZ");
  await expect(page.locator("#coverage-warning")).toContainText("No searchable departures");
  await expect(page.locator("#date")).toBeDisabled();
  await expect(page.locator("#search-button")).toBeDisabled();
});

check("counts follow the departure date and zero inbound disables every calendar", async (page) => {
  await open(page);
  await expect(page.locator("#origin-coverage")).toHaveText("Outbound: 1 on 2026-10-17");
  await expect(page.locator("#destination-coverage")).toContainText("Inbound: 5");
  await expect(page.locator("#origin-coverage")).not.toContainText("Inbound");
  await expect(page.locator("#destination-coverage")).not.toContainText("Outbound");
  await expect(page.locator("#date-zones")).toContainText("Arrivals use all available data");
  await page.getByLabel("To", { exact: true }).fill("XYZ");
  await expect(page.locator("#destination-coverage")).toContainText("Inbound: 0");
  await expect(page.locator("#coverage-warning")).toContainText("No searchable arrivals into XYZ");
  await expect(page.locator("#date")).toBeDisabled();
  await expect(page.locator('[name="last-date"]')).toBeDisabled();
  await expect(page.locator("#search-button")).toBeDisabled();
  await page.getByLabel("To", { exact: true }).fill("CDG");
  await expect(page.locator("#date")).toBeEnabled();
  await expect(page.locator("#search-button")).toBeEnabled();
});

check("slow dataset statistics do not block initial search", async (page) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/v1/catalog*", async (route) => {
    assert.equal(new URL(route.request().url()).searchParams.get("include_airports"), "false");
    await gate;
    await route.continue();
  });
  try {
    await open(page);
    await expect(page.locator("#origin-coverage")).toContainText("Outbound: 1");
    await expect(page.locator("#search-button")).toBeEnabled();
  } finally { release(); }
  await expect(page.locator("#dataset-tag")).toContainText("Published schedules");
});

check("statistics loaded before defaults follow the final flight-time selection", async (page) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch(), data = await response.json();
    await gate;
    await route.fulfill({ response, json: {
      ...data, default_time_basis: "observed", available_time_bases: ["scheduled", "observed"],
    } });
  });
  await page.route("**/v1/catalog*", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.dataset.time_bases = { scheduled: 0, observed: 3, actual: 0 };
    await route.fulfill({ response, json: data });
  });
  await page.route("**/v1/search-availability?*", (route) => fulfillAvailability(route, {
    dates: ["2026-10-17"], timezone: "America/Toronto",
  }));
  try {
    await page.goto(base);
    await expect(page.locator("#dataset-tag")).toContainText("Published schedules · 0");
  } finally { release(); }
  await expect(page.getByLabel("Flight times")).toHaveValue("observed");
  await expect(page.locator("#dataset-tag")).toContainText("Observed historical legs · 3");
});

check("changing departure date refreshes both directional counts", async (page) => {
  await page.route("**/v1/search-availability?*", async (route) => {
    const day = new URL(route.request().url()).searchParams.get("departure_date");
    await fulfillAvailability(route, {
      timezone: "America/Toronto", dates: ["2026-10-17", "2026-10-19"],
      outbound: day === "2026-10-19" ? 7 : 1,
      inbound: day === "2026-10-19" ? 2 : 5,
    });
  });
  await open(page);
  await chooseDate(page, "date", "2026-10-19");
  await expect(page.locator("#origin-coverage")).toHaveText("Outbound: 7 on 2026-10-19");
  await expect(page.locator("#destination-coverage")).toContainText("Inbound: 2");
});

check("delayed bootstrap preserves user input", async (page) => {
  let release, intercepted;
  const gate = new Promise((resolve) => { release = resolve; }),
    started = new Promise((resolve) => { intercepted = resolve; });
  await page.route("**/v1/bootstrap", async (route) => { intercepted(); await gate; await route.continue(); });
  await page.goto(base);
  await started;
  await page.getByLabel("From", { exact: true }).fill("LHR");
  await page.getByLabel("To", { exact: true }).fill("NRT");
  await openDataSettings(page);
  await page.getByLabel("Input clock").selectOption("UTC");
  release();
  await expect(page.locator("#origin-coverage")).toContainText("Outbound:");
  await expect(page.getByLabel("From", { exact: true })).toHaveValue("LHR");
  await expect(page.getByLabel("To", { exact: true })).toHaveValue("NRT");
});

check("date availability failures remain disabled until a retry succeeds", async (page) => {
  let fail = true;
  await page.route("**/v1/search-availability?*", (route) => fail
    ? route.fulfill({ status: 503, json: { detail: "Date data unavailable" } })
    : route.continue());
  await page.goto(demoSearch);
  await expect(page.locator("#date-availability")).toContainText("Date data unavailable");
  await expect(page.getByRole("button", { name: "Use UTC dates", exact: true })).toBeHidden();
  await expect(page.locator("#search-button")).toBeDisabled();
  fail = false;
  await page.getByRole("button", { name: "Retry departure dates" }).click();
  await expect(page.locator("#date-availability")).toContainText("America/Toronto");
  await expect(page.locator("#search-button")).toBeEnabled();
  await expect(page.locator("#retry-dates")).toBeHidden();
  await expect(page.getByLabel("Input clock")).toHaveValue("local");
});

for (const phone of [false, true]) check(
  `${phone ? "phone" : "desktop"} calendar recovers missing timezone through explicit UTC dates`,
  async (page) => {
    const searches = [], utcRequests = [];
    page.on("request", request => {
      if (request.method() === "POST" && searchPath.test(new URL(request.url()).pathname)) searches.push(request.url());
    });
    await page.route("**/v1/search-availability?*", async route => {
      const params = new URL(route.request().url()).searchParams;
      if (params.get("clock") === "local") return route.fulfill({status:422, json:{detail:{
        code:"airport_metadata_missing", message:"Load airport timezone metadata or choose UTC.",
      }}});
      utcRequests.push(params);
      await route.continue();
    });
    await page.goto(demoSearch + "&last-date=2026-10-19&minimum=45&ranking=shortest_elapsed");
    const recovery = page.getByRole("button", { name: "Use UTC dates", exact: true });
    await expect(recovery).toBeVisible();
    await expect(page.locator("#date-recovery-message")).toContainText("Local dates are unavailable");
    await expect(page.getByLabel("Input clock")).toHaveValue("local");
    await expect(page.locator("#date")).toBeDisabled();
    assert.equal(searches.length, 0);
    await recovery.click();
    await expect(page.getByLabel("Input clock")).toHaveValue("UTC");
    await expect(page.locator("#search-button")).toBeEnabled();
    await expect(page.locator("#date-clock-notice")).toContainText("Review the departure date");
    await expect(page.getByLabel("From", {exact:true})).toHaveValue("YYZ");
    await expect(page.getByLabel("To", {exact:true})).toHaveValue("CDG");
    await expect(page.locator('[name="ranking"]')).toHaveValue("shortest_elapsed");
    await expect(page.locator('[name="last-date"]')).toHaveValue("");
    await expect(recovery).toBeHidden();
    assert.ok(utcRequests.length > 0);
    assert.ok(utcRequests.every(params => !params.has("departure_date") && !params.has("departure_last_date")),
      "local dates cannot be silently reinterpreted as UTC");
    assert.equal(searches.length, 0, "recovery requires reviewing the refreshed date before search");
    const data = await search(page);
    assert.equal(data.query.origin, "YYZ");
    assert.equal(searches.length, 1);
  },
  phone ? {viewport:{width:390,height:844},isMobile:true,hasTouch:true} : {},
);

check("calendar recovery clears when a different origin has local dates", async page => {
  await page.route("**/v1/search-availability?*", async route => {
    if (new URL(route.request().url()).searchParams.get("origin") === "YYZ")
      return route.fulfill({status:422,json:{detail:{code:"airport_metadata_missing",message:"Missing timezone"}}});
    await route.continue();
  });
  await page.goto(demoSearch);
  await expect(page.locator("#date-recovery")).toBeVisible();
  await page.getByLabel("From", {exact:true}).fill("LHR");
  await expect(page.locator("#date")).toBeEnabled();
  await expect(page.locator("#date-recovery")).toBeHidden();
  await expect(page.getByLabel("Input clock")).toHaveValue("local");
});

check("calendar UTC review survives delayed bootstrap without submitting a search", async page => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const searches = [];
  page.on("request", request => {
    if (request.method() === "POST" && searchPath.test(new URL(request.url()).pathname)) searches.push(request.url());
  });
  await page.route("**/v1/bootstrap", async route => { await gate; await route.continue(); });
  await page.route("**/v1/search-availability?*", async route => {
    if (new URL(route.request().url()).searchParams.get("clock") === "local")
      return route.fulfill({status:422,json:{detail:{code:"airport_metadata_missing",message:"Missing timezone"}}});
    await route.continue();
  });
  try {
    await page.goto(demoSearch.replace("&date=2026-10-17", ""));
    await page.getByRole("button", {name:"Use UTC dates", exact:true}).click();
    await expect(page.locator("#date-clock-notice")).toContainText("Review the departure date");
    const bootstrap = page.waitForResponse("**/v1/bootstrap");
    release();
    await bootstrap;
    await expect(page.locator("#search-button")).toBeEnabled();
    await expect(page.locator("#date-clock-notice")).toBeVisible();
    await expect(page.getByLabel("Input clock")).toHaveValue("UTC");
    assert.equal(searches.length, 0);
  } finally { release(); }
});

check("calendar month controls stay open across sparse months and years", async (page) => {
  const searches = [];
  page.on("request", (request) => {
    if (searchPath.test(new URL(request.url()).pathname)) searches.push(request.url());
  });
  await page.route("**/v1/search-availability?*", (route) => fulfillAvailability(route, {
    origin: "YYZ", timezone: "America/Toronto",
    dates: ["2026-10-17", "2026-11-18", "2027-02-19"],
  }));
  await open(page);
  const calendar = page.locator("#date-calendar"),
    month = calendar.getByRole("button", { name: "Month with departure data" }),
    next = calendar.getByRole("button", { name: "Next month with data" }),
    previous = calendar.getByRole("button", { name: "Previous month with data" });
  await page.locator("#date").click();
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(calendar).toBeVisible();
  await expect(month).toHaveAttribute("data-value", "2026-11");
  await next.click();
  await expect(calendar).toBeVisible();
  await expect(month).toHaveAttribute("data-value", "2027-02");
  await expect(next).toBeDisabled();
  await previous.click();
  await expect(month).toHaveAttribute("data-value", "2026-11");
  await previous.click();
  await expect(calendar).toBeVisible();
  await expect(previous).toBeDisabled();
  await expect(month).toHaveAttribute("data-value", "2026-10");
  await month.click();
  await calendar.getByRole("button", { name: "October 2026", exact: true }).click();
  await expect(calendar).toBeVisible();
  await expect(page.locator("#date")).toHaveValue("2026-10-17");
  await expect(page.locator("#status")).not.toContainText("Inputs changed");
  // Actual clicks must reveal the chooser and change the displayed month.
  await month.click();
  await expect(calendar.getByRole("group", { name: "Available departure months" })).toBeVisible();
  await calendar.getByRole("button", { name: "November 2026", exact: true }).click();
  await expect(month).toHaveAttribute("data-value", "2026-11");
  await expect(calendar).toBeVisible();
  await month.press("ArrowUp");
  await expect(month).toHaveAttribute("data-value", "2026-10");
  await expect(calendar).toBeVisible();
  assert.equal(searches.length, 1, "browsing months must not submit the search form");
  await month.click();
  await calendar.getByRole("button", { name: "November 2026", exact: true }).click();
  await calendar.locator('[data-date="2026-11-18"]').click();
  await expect(calendar).toBeHidden();
  await expect(page.locator("#date")).toHaveValue("2026-11-18");
  await expect(page.locator("#date")).toBeFocused();
  await expect(page.locator("#search-button")).toBeEnabled();
});

check("last departure calendar navigates months within the selected range", async (page) => {
  await page.route("**/v1/search-availability?*", (route) => fulfillAvailability(route, {
    origin: "YYZ", timezone: "America/Toronto",
    dates: ["2026-10-17", "2026-11-01", "2026-11-16", "2026-12-01"],
  }));
  await open(page);
  await openSearchOptions(page);
  const input = page.locator('[name="last-date"]'),
    calendar = page.locator("#last-date-calendar"),
    month = calendar.getByRole("button", { name: "Month with departure data" });
  await input.click();
  await calendar.getByRole("button", { name: "Next month with data" }).click();
  await expect(calendar).toBeVisible();
  await expect(month).toHaveAttribute("data-value", "2026-11");
  await expect(calendar.locator('[data-date="2026-11-01"]')).toBeEnabled();
  await expect(calendar.locator('[data-date="2026-11-17"]')).toBeDisabled();
  await month.click();
  await expect(calendar.getByRole('button', { name: 'December 2026', exact: true })).toHaveCount(1);
  await month.press('Escape');
  await calendar.locator('[data-date="2026-11-01"]').click();
  await expect(input).toHaveValue("2026-11-01");
  await chooseDate(page, "date", "2026-10-17");
  await openSearchOptions(page);
  await input.click();
  await calendar.getByRole("button", { name: "Use first date only" }).click();
  await expect(input).toHaveValue("");
  await expect(calendar).toBeHidden();
});

check("date refresh does not steal focus when the traveler moves to another input", async (page) => {
  await page.route("**/v1/search-availability?*", (route) => fulfillAvailability(route, {
    timezone: "America/Toronto", dates: ["2026-10-17", "2026-10-19"],
  }));
  await open(page);
  let release, intercepted;
  const gate = new Promise((resolve) => { release = resolve; }),
    started = new Promise((resolve) => { intercepted = resolve; });
  await page.route("**/v1/search-availability?*", async (route) => {
    intercepted();
    await gate;
    await fulfillAvailability(route, {
      timezone: "America/Toronto", dates: ["2026-10-17", "2026-10-19"],
    });
  });
  try {
    await chooseDate(page, "date", "2026-10-19");
    await started;
    await page.getByLabel("To", { exact: true }).focus();
  } finally { release(); }
  await expect(page.locator("#search-button")).toBeEnabled();
  await expect(page.getByLabel("To", { exact: true })).toBeFocused();
});

check("availability and counts follow the selected flight times without duplicate requests", async (page) => {
  await page.route("**/v1/catalog*", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.dataset.time_bases = { scheduled: 12, observed: 3, actual: 1 };
    await route.fulfill({ response, json: data });
  });
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.available_time_bases = ["scheduled", "observed", "actual"];
    await route.fulfill({ response, json: data });
  });
  const dateRequests = [];
  await page.route("**/v1/search-availability?*", async (route) => {
    const params = new URL(route.request().url()).searchParams,
      basis = params.get("time_basis");
    dateRequests.push(basis);
    await fulfillAvailability(route, {
      origin: params.get("origin"),
      timezone: params.get("clock") === "UTC" ? "UTC" : "America/Toronto",
      dates: basis === "observed" ? ["2025-01-02"] : basis === "actual" ? [] : ["2026-10-17"],
    });
  });
  await open(page);
  await openDataSettings(page);
  const times = page.getByLabel("Flight times"),
    availability = page.locator("#date-availability");
  await expect(page.locator("#dataset-tag")).toContainText("Published schedules · 12");
  await openDataSettings(page);
  await times.selectOption("observed");
  await expect(availability).toContainText("Observed historical legs · America/Toronto");
  await expect(availability).toContainText("from YYZ");
  await expect(page.locator("#date")).toHaveValue("2025-01-02");
  await expect(page.locator("#dataset-tag")).toContainText("Observed historical legs · 3");
  await expect(page.locator("#applied-query")).toContainText("Published schedules");
  await search(page);
  await expect(page.locator("#applied-query")).toContainText("Observed historical legs");
  await openDataSettings(page);
  await times.selectOption("actual");
  await expect(availability).toContainText("No selectable departure dates for YYZ → CDG");
  await expect(page.locator("#date")).toBeDisabled();
  await expect(page.locator("#date")).toHaveValue("2025-01-02");
  await expect(page.locator("#search-button")).toBeDisabled();
  await expect(page.locator("#dataset-tag")).toContainText("Reported actual times · 1");
  await openDataSettings(page);
  await times.selectOption("scheduled");
  await expect(page.locator("#date")).toHaveValue("2026-10-17");
  await expect(page.locator("#search-button")).toBeEnabled();
  assert.deepEqual(dateRequests, ["scheduled", "observed", "actual", "scheduled"]);
});

check("late availability responses cannot overwrite a new origin and clock", async (page) => {
  await open(page);
  let release, started, completed;
  const delayed = new Promise((resolve) => { release = resolve; }),
    intercepted = new Promise((resolve) => { started = resolve; }),
    finished = new Promise((resolve) => { completed = resolve; });
  await page.route("**/v1/search-availability?*", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get("origin") !== "LHR") return route.continue();
    if (params.get("clock") === "local") {
      started();
      await delayed;
      try {
        await fulfillAvailability(route, {
          origin: "LHR", timezone: "Europe/London", dates: ["2025-01-01"],
        });
      } finally {
        completed();
      }
    } else {
      await fulfillAvailability(route, {
        origin: "LHR", timezone: "UTC", dates: ["2026-10-18", "2026-11-02"],
      });
    }
  });
  await page.getByLabel("From", { exact: true }).fill("LHR");
  await intercepted;
  await expect(page.locator("#search-button")).toBeDisabled();
  await expect(page.locator("#date")).toBeDisabled();
  await openDataSettings(page);
  await page.getByLabel("Input clock").selectOption("UTC");
  await expect(page.locator("#date")).toHaveValue("2026-10-18");
  await expect(page.locator("#search-button")).toBeEnabled();
  release();
  await finished;
  await expect(page.locator("#date-availability")).toContainText("from LHR · Published schedules · UTC");
  await expect(page.locator("#date-zones")).toContainText("UTC");
  await expect(page.locator("#date")).toHaveValue("2026-10-18");
  await page.locator("#date").click();
  await expect(page.locator('#date-calendar [data-date="2026-10-18"]')).toBeEnabled();
});

check("phone calendar can switch months by touch without overflowing", async (page) => {
  await page.route("**/v1/search-availability?*", (route) => fulfillAvailability(route, {
    origin: "YYZ", timezone: "America/Toronto", dates: ["2026-10-17", "2026-11-18"],
  }));
  await open(page);
  const calendar = page.locator("#date-calendar");
  await page.locator("#date").tap();
  await calendar.getByRole("button", { name: "Next month with data" }).tap();
  await expect(calendar).toBeVisible();
  await expect(calendar.getByRole("button", { name: "Month with departure data" })).toHaveAttribute("data-value", "2026-11");
  await expect(calendar.getByRole("button", { name: "Next month with data" })).toBeDisabled();
  await calendar.getByRole("button", { name: "Previous month with data" }).tap();
  await expect(calendar).toBeVisible();
  await expect(calendar.getByRole("button", { name: "Month with departure data" })).toHaveAttribute("data-value", "2026-10");
  await calendar.getByRole("button", { name: "Month with departure data" }).tap();
  await calendar.getByRole("button", { name: "November 2026", exact: true }).tap();
  await expect(calendar).toBeVisible();
  await expect(calendar.getByRole("button", { name: "Month with departure data" })).toHaveAttribute("data-value", "2026-11");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const bounds = await calendar.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 320);
  await calendar.locator('[data-date="2026-11-18"]').tap();
  await expect(page.locator("#date")).toHaveValue("2026-11-18");
  await expect(calendar).toBeHidden();
}, { viewport: { width: 320, height: 800 }, isMobile: true, hasTouch: true });

check("shared UTC search renders before calendar availability and background statistics", async (page) => {
  let releaseDates, releaseStatistics;
  const datesGate = new Promise(resolve => { releaseDates = resolve; }),
    statisticsGate = new Promise(resolve => { releaseStatistics = resolve; });
  let bootstrapRequests = 0, searches = 0;
  page.on("request", request => {
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/v1/bootstrap") bootstrapRequests++;
    if (searchPath.test(pathname)) searches++;
  });
  await page.route("**/v1/search-availability?*", async route => {
    await datesGate;
    await route.continue();
  });
  await page.route("**/v1/catalog*", async route => {
    await statisticsGate;
    await route.continue();
  });
  try {
    const data = await result(page, searchPath, () => page.goto(
      `${base}?origin=YYZ&destination=CDG&date=2026-10-17&stops=any&input-clock=UTC&time-basis=scheduled`,
    ));
    assert.ok(data.items.length);
    assert.equal(data.query.departure_start, "2026-10-17T00:00:00Z");
    await expect(page.locator("[data-option]").first()).toBeVisible();
    await expect(page.locator("#date")).toHaveValue("2026-10-17");
    await expect(page.locator("#date")).toBeDisabled();
    assert.equal(bootstrapRequests, 0, "bootstrap cannot crowd out the initial interactive reads");
    const bootstrap = page.waitForResponse("**/v1/bootstrap");
    releaseDates();
    await bootstrap;
    await expect(page.locator("#search-button")).toBeEnabled();
    await expect(page.locator("#request-progress")).toBeHidden();
    await expect(page.locator("#date")).toHaveValue("2026-10-17");
    assert.equal(searches, 1, "availability and bootstrap must not rerun the shared search");
  } finally {
    releaseDates();
    releaseStatistics();
  }
});

check("linked search disables calendar before statistics load when destination has no arrivals", async (page) => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; }), searches = [];
  await page.route("**/v1/catalog*", async (route) => { await gate; await route.continue(); });
  page.on("request", (r) => { if (searchPath.test(new URL(r.url()).pathname)) searches.push(r.url()); });
  try {
    await page.goto(`${base}?origin=YYZ&destination=XYZ&date=2026-10-17&stops=any&input-clock=UTC&time-basis=scheduled`);
    await expect(page.locator("#coverage-warning")).toContainText("No searchable arrivals into XYZ");
    await expect(page.locator("#date")).toBeDisabled();
    await expect(page.locator('[name="last-date"]')).toBeDisabled();
    await expect(page.locator("#origin-coverage")).toContainText("Outbound: 1");
    await expect(page.locator("#search-button")).toBeDisabled();
    assert.equal(searches.length, 1, "the server evaluates the shared query independently of calendar counts");
  } finally { release(); }
});

check("endpoint airports are excluded from via suggestions and rejected when typed", async (page) => {
  await open(page);
  await openSearchOptions(page);
  const via = page.getByLabel("Via airports", { exact: true });
  await openSearchOptions(page);
  await via.fill("Y");
  await expect(page.locator('#via-suggestions [data-value="YYZ"]')).toHaveCount(0);
  await openSearchOptions(page);
  await via.fill("C");
  await expect(page.locator('#via-suggestions [data-value="CDG"]')).toHaveCount(0);
  for (const code of ["YYZ", "CDG"]) {
    await openSearchOptions(page);
    await via.fill(code);
    await openSearchOptions(page);
    await via.press("Enter");
    await expect(page.locator("#error")).toContainText("Via airports must be intermediate airports");
  }
});

async function exported(page) {
  if (await page.locator("#search-details").evaluate(details => details.open))
    await page.locator("#search-details > summary").click();
  const pending = page.waitForEvent("download");
  await revealControl(page.locator("#export"));
  await page.getByRole("button", { name: "Export JSON", exact: true }).click();
  const stream = await (await pending).createReadStream(),
    chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString());
}
async function edit(page) {
  await page
    .getByRole("button", { name: "Change connection", exact: true })
    .first()
    .click();
  await expect(page.getByLabel("Next airport", { exact: true })).toBeFocused();
}
async function next(
  page,
  action = () =>
    page
      .getByRole("button", { name: "Find next flights", exact: true })
      .click(),
) {
  const data = await result(page, /\/v1\/plans\/rebuild$/, action);
  await expect(
    page.getByRole("button", { name: "Find next flights", exact: true }),
  ).toBeEnabled();
  return data;
}
async function viaSearch(page, input = "ams", expected = ["AMS"]) {
  await open(page);
  await openSearchOptions(page);
  await page.getByLabel("Via airports", { exact: true }).fill(input);
  const data = await search(page, () =>
    page.getByLabel("Via airports", { exact: true }).press("Enter"),
  );
  assert.deepEqual(data.query.via_airport, expected);
  assert.ok(data.items.length);
  assert.ok(
    data.items.every((item) =>
      expected.every((code) =>
        item.legs.some((flight) => flight.destination_iata === code),
      ),
    ),
  );
  return data;
}

check("next-flight pages append options without changing the selected journey", async (page) => {
  const requests = [];
  await page.route("**/v1/plans/rebuild", async (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    await route.continue({ postData: JSON.stringify({ ...body, option_limit: 1 }) });
  });
  await viaSearch(page);
  const before = identity((await exported(page)).selection.legs);
  await edit(page);
  const first = await next(page);
  await expect(page.locator("#more-next")).toBeVisible();
  await result(page, /\/v1\/plans\/rebuild$/, () => page.locator("#more-next").click());
  await expect(page.locator("[data-next]")).toHaveCount(2);
  assert.equal(requests.at(-1).option_offset, first.next_option_offset);
  assert.equal(requests.at(-1).data_revision, first.data_revision);
  assert.deepEqual(identity((await exported(page)).selection.legs), before);
});

check(
  "departure dates set the search window and discard old arrival inputs",
  async (page) => {
    const submitted = [];
    page.on("request", (request) => {
      if (request.method() === "POST")
        submitted.push({
          path: new URL(request.url()).pathname,
          body: request.postDataJSON(),
        });
    });
    const oldLink = new URL(base);
    oldLink.search = new URLSearchParams({
      origin: "YYZ",
      destination: "CDG",
      via: "AMS",
      date: "2026-10-17",
      arrival: "2026-10-17T00:00",
      "arrival-fold": "1",
    });
    const initial = await result(page, searchPath, () => page.goto(oldLink.href));
    await expect(page.locator("[data-option]").first()).toBeVisible();
    await expect(page.getByLabel("Arrive by", { exact: true })).toHaveCount(0);
    await expect(
      page.getByLabel("Repeated arrival time", { exact: true }),
    ).toHaveCount(0);
    await expect(page.locator("#date-zones")).toContainText("all available data");
    assert.equal(initial.query.departure_start, "2026-10-17T04:00:00Z");
    const checkWindow = (query) =>
      assert.equal(
        Date.parse(query.horizon_end) - Date.parse(query.departure_end),
        Math.max(Date.parse("2026-10-19T05:30:00Z"), Date.parse(query.departure_end)) - Date.parse(query.departure_end),
      );
    checkWindow(initial.query);
    assert.equal(new URL(page.url()).searchParams.has("arrival"), false);
    assert.equal(new URL(page.url()).searchParams.has("arrival-fold"), false);

    await openSearchOptions(page);
    await page.getByLabel("From", { exact: true }).fill("LHR");
    await expect(page.locator("#date-availability")).toContainText("2 departure dates");
    await chooseDate(page, "last-date", "2026-10-19");
    const ranged = await search(page);
    assert.equal(ranged.query.departure_end, "2026-10-19T23:00:00Z");
    checkWindow(ranged.query);
    const saved = await exported(page);
    assert.equal(saved.query_inputs["last-date"], "2026-10-19");
    assert.equal(Object.hasOwn(saved.query_inputs, "arrival"), false);
    assert.equal(Object.hasOwn(saved.query_inputs, "arrival-fold"), false);
    assert.equal(saved.query.horizon_end, ranged.query.horizon_end);
    // Verify the app's exact link and its real write, without reading the OS
    // clipboard: Linux WebKit does not grant that separate read permission.
    await page.evaluate(() => {
      const write = navigator.clipboard.writeText.bind(navigator.clipboard);
      navigator.clipboard.writeText = async text => {
        await write(text);
        window.copiedSearchLink = text;
      };
    });
    await page
      .getByRole("button", { name: "Share search", exact: true })
      .click();
    await expect(page.locator("#status")).toContainText("Search link copied");
    const copied = new URL(
      await page.evaluate(() => window.copiedSearchLink),
    );
    assert.equal(copied.searchParams.get("last-date"), "2026-10-19");
    assert.equal(copied.searchParams.has("arrival"), false);
    assert.equal(copied.searchParams.has("arrival-fold"), false);

    await edit(page);
    await page.getByLabel("Next departure").selectOption("any");
    const plan = await next(page);
    assert.ok(plan.options.length);
    assert.equal(submitted.at(-1).body.horizon_end, ranged.query.horizon_end);
    await result(page, /\/v1\/plans\/rebuild$/, () =>
      page.locator("[data-next]").first().click(),
    );
    assert.equal(submitted.at(-1).body.horizon_end, ranged.query.horizon_end);

    await openDataSettings(page);
    await page.getByLabel("Input clock").selectOption("UTC");
    const utc = await search(page);
    assert.equal(utc.query.departure_start, "2026-10-18T00:00:00Z");
    assert.equal(utc.query.departure_end, "2026-10-20T00:00:00Z");
    checkWindow(utc.query);
    for (const { path, body } of submitted.filter(({ path }) => searchPath.test(path))) {
      assert.ok(
        !Object.keys(body).some((key) => key.startsWith("arrival_")),
        path,
      );
    }
  },
  { permissions: process.env.FLYJI_QA_BROWSER === "webkit" ? [] : ["clipboard-write"] },
);

check(
  "typed constraints survive search links and exports",
  async (page, context) => {
    const data = await viaSearch(page),
      selected = await exported(page);
    assert.deepEqual(
      identity(selected.selection.legs),
      identity(data.items[0].legs),
    );
    assert.deepEqual(selected.query.via_airport, ["AMS"]);
    assert.equal(selected.coverage, "unknown");
    assert.equal(selected.selection.diagnostics.snapshot_id, undefined);
    assert.ok(selected.data_licence_notice.endsWith("/data-sources"));
    assert.equal(new URL(page.url()).searchParams.get("via"), "AMS");
    const reloaded = await context.newPage();
    await reloaded.goto(page.url());
    await expect(reloaded.locator("[data-option]").first()).toBeVisible();
    await expect(
      reloaded.getByLabel("Via airports", { exact: true }),
    ).toHaveValue("AMS");
  },
);

check(
  "connection edits preserve prefix rebuild suffix and undo",
  async (page) => {
    await viaSearch(page, "lhr ams", ["LHR", "AMS"]);
    const before = (await exported(page)).selection;
    await edit(page);
    await page.getByLabel("Next departure").selectOption("any");
    await page.getByLabel("Allow a same-city airport change").check();
    await page.getByLabel("Next airport", { exact: true }).fill("ams");
    const plan = await next(page);
    assert.ok(plan.options.length);
    assert.ok(
      plan.options.every((option) => option.flight.destination_iata === "AMS"),
    );
    const index = plan.options.findIndex(
      (option) =>
        option.complete &&
        option.connection.airport_change &&
        Date.parse(option.flight.departure_scheduled) >
          Date.parse(before.legs[1].departure_scheduled),
    );
    assert.ok(
      index >= 0,
      "fixture offers a later complete airport-change continuation",
    );
    const updated = await result(page, /\/v1\/plans\/rebuild$/, () =>
      page.locator(`[data-next="${index}"]`).click(),
    );
    await expect(page.locator("#connection-editor")).toBeHidden();
    assert.deepEqual(
      identity(updated.legs.slice(0, 1)),
      identity(before.legs.slice(0, 1)),
    );
    assert.deepEqual(
      identity(updated.legs.slice(1)),
      identity([plan.options[index].flight, ...plan.options[index].tail]),
    );
    assert.deepEqual(
      identity((await exported(page)).selection.legs),
      identity(updated.legs),
    );
    assert.deepEqual((await exported(page)).query.via_airport, ["LHR", "AMS"]);
    await result(page, searchPath, () => page.locator('[name="ranking"]').selectOption("shortest_elapsed"));
    await expect(page.locator("#journey-status")).toContainText("Edited journey");
    assert.deepEqual(identity((await exported(page)).selection.legs), identity(updated.legs));
    await expect(page.getByRole("button", { name: "Undo edit", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Undo edit", exact: true }).click();
    assert.deepEqual(
      identity((await exported(page)).selection.legs),
      identity(before.legs),
    );
    await expect(page.locator("#undo")).toBeDisabled();
  },
);

check("ordered via lists survive typing, links and export", async (page, context) => {
  await open(page);
  await openSearchOptions(page);
  const via = page.getByLabel("Via airports", { exact: true });
  await openSearchOptions(page);
  await via.pressSequentially("lhr ams");
  await expect(via).toHaveValue("LHR AMS");
  const data = await search(page, () => via.press("Enter"));
  assert.deepEqual(data.query.via_airport, ["LHR", "AMS"]);
  assert.ok(data.items.length);
  for (const item of data.items)
    assert.deepEqual(item.legs.map((f) => f.destination_iata), ["LHR", "AMS", "CDG"]);
  await expect(via).toHaveValue("LHR, AMS");
  await expect(page.locator("#applied-query")).toContainText("via LHR → AMS");
  assert.equal(new URL(page.url()).searchParams.get("via"), "LHR, AMS");
  assert.deepEqual((await exported(page)).query.via_airport, ["LHR", "AMS"]);
  const reloaded = await context.newPage();
  await result(reloaded, searchPath, () => reloaded.goto(page.url()));
  await expect(reloaded.locator("[data-option]").first()).toBeVisible();
  await expect(reloaded.getByLabel("Via airports", { exact: true })).toHaveValue(
    "LHR, AMS",
  );
  await openSearchOptions(page);
  await via.fill(" ams,, lhr, ");
  const reversed = await search(page, () => via.press("Enter"));
  assert.deepEqual(reversed.query.via_airport, ["AMS", "LHR"]);
  assert.deepEqual(reversed.items, []);
  assert.equal(reversed.diagnostics.exhaustive, true);
});

check("via suggestions preserve other codes and invalid lists preserve the journey", async (page) => {
  await viaSearch(page, "LHR, AMS", ["LHR", "AMS"]);
  const via = page.getByLabel("Via airports", { exact: true }),
    before = (await exported(page)).selection;
  await openSearchOptions(page);
  await via.fill("LHR, A");
  const appended = await page
    .locator("#via-suggestions [role=option]")
    .evaluateAll((options) => options.map((o) => o.dataset.value));
  assert.ok(appended.includes("LHR, AMS"));
  assert.ok(!appended.includes("LHR, LHR"));
  await openSearchOptions(page);
  await via.press("ArrowDown");
  await expect(via).toHaveAttribute("aria-activedescendant", "via-option-0");
  await openSearchOptions(page);
  await via.press("Enter");
  await expect(via).toHaveValue("LHR, AMS");
  await expect(via).toBeFocused();
  await expect(via).toHaveAttribute("aria-expanded", "false");
  await openSearchOptions(page);
  await via.fill("LHR, LG, KEF");
  await via.evaluate((input) => input.setSelectionRange(6, 6));
  await openSearchOptions(page);
  await via.press("ArrowRight");
  const replaced = await page
    .locator("#via-suggestions [role=option]")
    .evaluateAll((options) => options.map((o) => o.dataset.value));
  assert.ok(replaced.includes("LHR, LGW, KEF"));
  assert.ok(!replaced.includes("LHR, KEF, KEF"));
  await page.getByRole("option", { name: /^LGW / }).click();
  await expect(via).toHaveValue("LHR, LGW, KEF");
  await expect(via).toBeFocused();
  await expect(via).toHaveAttribute("aria-expanded", "false");
  assert.equal(await via.evaluate((input) => input.selectionStart), 8);
  await openSearchOptions(page);
  await via.fill("LHR, ");
  const choices = page.locator("#via-suggestions [role=option]");
  assert.ok(await choices.count() > 1);
  await openSearchOptions(page);
  await via.press("ArrowUp");
  await expect(via).toHaveAttribute("aria-activedescendant", await choices.last().getAttribute("id"));
  await openSearchOptions(page);
  await via.press("ArrowDown");
  await expect(via).toHaveAttribute("aria-activedescendant", await choices.first().getAttribute("id"));
  await openSearchOptions(page);
  await via.press("Escape");
  await expect(via).toHaveValue("LHR, ");
  await expect(via).toHaveAttribute("aria-expanded", "false");
  await openSearchOptions(page);
  await via.press("ArrowDown");
  await openSearchOptions(page);
  await via.press("Tab");
  await expect(via).not.toBeFocused();
  await expect(via).toHaveValue("LHR, ");
  await expect(via).toHaveAttribute("aria-expanded", "false");
  for (const [value, error] of [
    ["LHR, lhr", "must not repeat"],
    ["LHR, FRA1", "Invalid airport code: FRA1"],
    ["YYZ, AMS", "endpoint"],
  ]) {
    await openSearchOptions(page);
    await via.fill(value);
    await openSearchOptions(page);
    await via.press("Enter");
    await expect(page.locator("#error")).toContainText(error);
    await expect(page.locator("#error")).toBeVisible();
    assert.deepEqual(
      identity((await exported(page)).selection.legs),
      identity(before.legs),
    );
  }
  await openSearchOptions(page);
  await page.locator("#search-form [name=stops]").selectOption("3");
  await openSearchOptions(page);
  await via.fill("LHR, AMS, KEF, YUL");
  await openSearchOptions(page);
  await via.press("Enter");
  await expect(page.locator("#error")).toContainText("choose Any number");
  assert.deepEqual(
    identity((await exported(page)).selection.legs),
    identity(before.legs),
  );
});

check(
  "connection bounds and avoided airports reach the engine",
  async (page) => {
    await open(page);
    await openSearchOptions(page);
    await page
      .locator("#search-form")
      .getByLabel("Minimum connection (minutes)")
      .fill("120");
    await page.getByLabel("Maximum connection (days)").fill("");
    await openSearchOptions(page);
    await page.getByLabel("Avoid airports").fill("ams");
    const data = await search(page);
    assert.equal(data.query.minimum_connection_minutes, 120);
    assert.equal(data.query.maximum_connection_minutes, null);
    assert.deepEqual(data.query.avoid_airports, ["AMS"]);
    assert.ok(data.items.length);
    assert.ok(
      data.items.every(
        (item) =>
          item.connections.every((gap) => gap.wait_minutes >= 120) &&
          item.legs.every(
            (f) => ![f.origin_iata, f.destination_iata].includes("AMS"),
          ),
      ),
    );
    await edit(page);
    const plan = await next(page);
    assert.ok(
      plan.options.every((option) => option.flight.destination_iata !== "AMS"),
    );
  },
);

check("rejected stale choice keeps the selected journey", async (page) => {
  await viaSearch(page);
  const before = identity((await exported(page)).selection.legs);
  await edit(page);
  await next(page);
  await page.route("**/v1/plans/rebuild", (route) =>
    route.request().postDataJSON().chosen_next
      ? route.fulfill({
          status: 422,
          json: {
            detail: {
              code: "prefix_cancelled",
              message: "Selected flight is unavailable.",
            },
          },
        })
      : route.continue(),
  );
  await page.locator("[data-next]").first().click();
  await expect(page.locator("#editor-error")).toBeVisible();
  assert.deepEqual(identity((await exported(page)).selection.legs), before);
  await expect(page.locator("[data-next]").first()).toBeEnabled();
});

check("partial searches differ from exhaustive empty results", async (page) => {
  await page.route("**/v1/itineraries/search/local", async (route) => {
    const response = await route.fetch({
      postData: JSON.stringify({
        ...route.request().postDataJSON(),
        limits: { max_candidates: 1 },
      }),
    });
    await route.fulfill({ response });
  });
  const data = await open(page, false);
  assert.equal(data.diagnostics.exhaustive, false);
  await expect(page.locator("#partial")).toBeVisible();
  await expect(page.locator("#options")).not.toContainText(
    /no matching route/i,
  );
  await page.unroute("**/v1/itineraries/search/local");
  await page.getByLabel("To", { exact: true }).fill("NRT");
  await openSearchOptions(page);
  await page.getByLabel("Maximum connection (days)").fill("0.25");
  await openDataSettings(page);
  await page.getByLabel("Input clock").selectOption("UTC");
  const empty = await search(page);
  assert.equal(empty.diagnostics.exhaustive, true);
  assert.deepEqual(empty.items, []);
  await expect(page.locator("#partial")).toBeHidden();
  await expect(page.locator("[data-option]")).toHaveCount(0);
  await expect(page.locator("#export")).toBeDisabled();
  await expect(page.locator(".map-panel")).toBeVisible();
  await expect(page.locator("#route-map .map-route")).toHaveCount(0);
});

check("storage failure can be retried", async (page) => {
  let fail = true;
  await page.route("**/v1/bootstrap", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: {
            detail: {
              code: "storage_unavailable",
              message: "Storage unavailable.",
            },
          },
        })
      : route.continue(),
  );
  await page.goto(demoSearch);
  await expect(page.getByRole("alert")).toBeVisible();
  fail = false;
  await page
    .getByRole("button", { name: "Retry data connection", exact: true })
    .click();
  await expect(page.locator("[data-option]").first()).toBeVisible();
  await expect(page.locator("#error")).toBeHidden();
});

check(
  "UTC search works without suggestions and ignores browser timezone",
  async (page) => {
    await page.route("**/v1/bootstrap", async (route) => {
      const response = await route.fetch(),
        data = await response.json();
      await route.fulfill({ response, json: { ...data, airports: [] } });
    });
    await page.goto(base);
    await expect(
      page.getByRole("button", { name: "Search flights", exact: true }),
    ).toBeDisabled();
    await page.getByLabel("From", { exact: true }).fill("yyz");
    await page.getByLabel("To", { exact: true }).fill("cdg");
    await openDataSettings(page);
    await page.getByLabel("Input clock").selectOption("UTC");
    await expect(page.locator("#date-availability")).toContainText("UTC");
    const date = await page
      .getByLabel("Departure date", { exact: true })
      .inputValue();
    const data = await search(page);
    assert.equal(data.query.origin, "YYZ");
    assert.equal(data.query.destination, "CDG");
    assert.equal(
      Date.parse(data.query.departure_start),
      Date.parse(date + "T00:00:00Z"),
    );
    assert.ok(data.items.length);
  },
);

check(
  "bounded connection search retains the original journey",
  async (page) => {
    await viaSearch(page);
    const before = identity((await exported(page)).selection.legs);
    await page.route("**/v1/plans/rebuild", async (route) => {
      const response = await route.fetch({
        postData: JSON.stringify({
          ...route.request().postDataJSON(),
          limits: { max_candidates: 1 },
        }),
      });
      await route.fulfill({ response });
    });
    await edit(page);
    await page.getByLabel("Next airport", { exact: true }).fill("CDG");
    const plan = await next(page);
    assert.equal(plan.diagnostics.exhaustive, false);
    assert.deepEqual(identity((await exported(page)).selection.legs), before);
    await expect(page.locator("#editor-status")).toContainText(/partial/i);
  },
);

check(
  "map controls and clock changes preserve the selected flights",
  async (page) => {
    await open(page);
    const before = identity((await exported(page)).selection.legs);
    await expect(
      page.getByRole("img", { name: /flight route|world map/i }),
    ).toBeVisible();
    await expect(page.locator("#map-error")).toBeHidden();
    await openMapControls(page);
    await page.getByRole("button", { name: "Map", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Map", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Earth", exact: true }).click();
    const mapImage = page.getByRole("img", { name: /flight route|world map/i });
    await expect(mapImage.locator("path").first()).toBeAttached();
    await expect(mapImage).toHaveAttribute("data-mode", "globe");
    const cameraBefore = await mapImage.locator(".map-land").getAttribute("d");
    await page
      .getByRole("button", { name: "Tilt globe up", exact: true })
      .click();
    await expect
      .poll(async () => await mapImage.locator(".map-land").getAttribute("d") !== cameraBefore)
      .toBe(true);
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page
      .getByRole("button", { name: "Fit journey", exact: true })
      .click();
    await openJourneyTimeline(page);
    await page.getByRole("slider", { name: /^Journey time/ }).fill("500");
    const cursor = await page
      .getByRole("slider", { name: /^Journey time/ })
      .inputValue();
    await page.getByLabel("Leg times").selectOption("UTC");
    assert.deepEqual(identity((await exported(page)).selection.legs), before);
    assert.equal(
      await page.getByRole("slider", { name: /^Journey time/ }).inputValue(),
      cursor,
    );
  },
);

check("keyboard can select edit apply and undo", async (page) => {
  await viaSearch(page);
  const option = page.locator("[data-option]").first();
  await option.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-option]").first()).toBeFocused();
  const before = identity((await exported(page)).selection.legs);
  const editButton = page
    .getByRole("button", { name: "Change connection", exact: true })
    .first();
  await editButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Next airport", { exact: true })).toBeFocused();
  await next(page, () =>
    page.getByLabel("Next airport", { exact: true }).press("Enter"),
  );
  await page.locator("[data-next]").first().focus();
  await result(page, /\/v1\/plans\/rebuild$/, () =>
    page.keyboard.press("Enter"),
  );
  await expect(
    page.getByRole("button", { name: "Undo edit", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  assert.deepEqual(identity((await exported(page)).selection.legs), before);
});

check(
  "phone workspace remains usable without horizontal overflow",
  async (page) => {
    await open(page);
    await openSearchOptions(page);
    const via = page.getByLabel("Via airports", { exact: true });
    await openSearchOptions(page);
    await via.fill("LHR, A");
    await page.getByRole("option", { name: /^AMS / }).tap();
    await expect(via).toHaveValue("LHR, AMS");
    await expect(via).toHaveAttribute("aria-expanded", "false");
    const views = page.getByRole("navigation", { name: "Workspace view" });
    for (const width of [320, 390, 736, 1180]) {
      await page.setViewportSize({ width, height: 900 });
      await openSearchOptions(page);
      await via.fill("LHR, ");
      await expect(via).toHaveAttribute("aria-expanded", "true");
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await openSearchOptions(page);
      await via.press("Escape");
      if (await views.isVisible()) {
        await views.getByRole("button", { name: "Journey", exact: true }).tap();
        await revealControl(page.locator("#export"));
        await expect(
          page.getByRole("button", { name: "Export JSON", exact: true }),
        ).toBeVisible();
        await views.getByRole("button", { name: "Map", exact: true }).tap();
        await openMapControls(page);
        await expect(
          page.getByRole("button", { name: "Fit journey", exact: true }),
        ).toBeVisible();
      }
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
    }
  },
  { viewport: { width: 390, height: 900 }, isMobile: true, hasTouch: true },
);

check(
  "not-before time resolves in the airport zone and reopening resets it",
  async (page) => {
    await viaSearch(page);
    const selected = (await exported(page)).selection;
    const first = selected.legs[0],
      airport = selected.airports.find(
        (a) => a.iata === first.destination_iata,
      );
    const threshold = Date.parse(first.arrival_scheduled) + 6 * 60 * 60 * 1000;
    const { localInput } = await import("../src/server/web/format.mjs");
    await edit(page);
    await page.getByLabel("Next departure").selectOption("after");
    await page
      .getByLabel("Not before", { exact: true })
      .fill(localInput(threshold, airport.timezone));
    const pending = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/v1/plans/rebuild",
    );
    const resolved = await result(page, /\/v1\/time\/resolve$/, () =>
      page
        .getByRole("button", { name: "Find next flights", exact: true })
        .click(),
    );
    assert.equal(Date.parse(resolved.utc), threshold);
    const plan = await (await pending).json();
    await expect(
      page.getByRole("button", { name: "Find next flights", exact: true }),
    ).toBeEnabled();
    assert.ok(plan.options.length);
    assert.ok(
      plan.options.every(
        (o) => Date.parse(o.flight.departure_scheduled) >= threshold,
      ),
    );
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await edit(page);
    await expect(
      page.getByLabel("Not before", { exact: true }),
    ).toHaveJSProperty("required", false);
    await next(page);
  },
);

check("stale catalog timezone is corrected by date availability before search", async (page) => {
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.airports.find((a) => a.iata === "YYZ").timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: data });
  });
  const data = await open(page);
  assert.equal(data.departure_timezone, "America/Toronto");
  await expect(page.locator("#date-zones")).toContainText("America/Toronto");
  await expect(page.locator("#error")).toBeHidden();
});

check("editor timezone errors refresh input clocks while selected facts stay frozen", async (page) => {
  await page.route("**/v1/itineraries/search/local", async route => {
    const response = await route.fetch(), data = await response.json();
    data.airports.find(airport => airport.iata === "LHR").timezone = "Asia/Tokyo";
    await route.fulfill({response,json:data});
  });
  await open(page);
  const before = identity((await exported(page)).selection.legs);
  await edit(page);
  await page.getByLabel("Next departure").selectOption("after");
  await page.getByLabel("Not before", {exact:true}).fill("2026-10-18T09:00");
  const failed = page.waitForResponse(response => new URL(response.url()).pathname === "/v1/time/resolve");
  await page.locator("#find-next").click();
  const rejected = await failed;
  assert.equal(rejected.status(), 422);
  assert.equal((await rejected.json()).detail.code, "airport_timezone_changed");
  await expect(page.locator("#editor-zone")).toContainText("Europe/London");
  await expect(page.locator("#find-next")).toBeEnabled();
  await next(page);
  await expect(page.locator("#editor-error")).toBeHidden();
  assert.deepEqual(identity((await exported(page)).selection.legs), before);
  assert.equal((await exported(page)).selection.airports.find(airport => airport.iata === "LHR").timezone, "Asia/Tokyo");
});

check("timezone changes after loading dates are corrected before retry", async (page) => {
  let actualZone, departureDate;
  await page.route("**/v1/bootstrap", async (route) => {
    const response = await route.fetch(),
      catalog = await response.json();
    const origin = catalog.airports.find((a) => a.iata === "YYZ");
    actualZone = origin.timezone;
    const { localDate } = await import("../src/server/web/format.mjs");
    departureDate = localDate(catalog.first_departure, actualZone);
    origin.timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: catalog });
  });
  await page.route("**/v1/search-availability?*", async (route) => {
    const response = await route.fetch(), data = await response.json();
    data.timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: data });
  });
  const pending = page.waitForResponse((r) =>
    searchPath.test(new URL(r.url()).pathname),
  );
  await page.goto(demoSearch);
  const failed = await pending;
  assert.equal(failed.status(), 422);
  assert.equal((await failed.json()).detail.code, "airport_timezone_changed");
  await expect(page.locator("#error")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Search flights", exact: true }),
  ).toBeEnabled();
  // The error asks the traveler to review dates; retain explicit input rather
  // than silently shifting it when corrected metadata changes the clock.
  await chooseDate(page, "date", departureDate);
  const retried = await search(page);
  assert.equal(retried.departure_timezone, actualZone);
  assert.ok(retried.items.length);
  await expect(page.locator("#error")).toBeHidden();
});

check(
  "map failure leaves flight inspection and editing available",
  async (page) => {
    await page.route("**/world-110m.geojson", (route) =>
      route.fulfill({ status: 503, body: "Geometry unavailable" }),
    );
    await viaSearch(page);
    await expect(page.locator("#map-error")).toBeVisible();
    assert.ok((await exported(page)).selection.legs.length);
    await edit(page);
    const plan = await next(page);
    assert.ok(plan.options.length);
  },
);

check("slow searches show indeterminate activity, elapsed time and a map overlay", async (page) => {
  await open(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route("**/v1/itineraries/search/local", async route => { await gate; await route.continue(); });
  const pending = page.waitForResponse(r => searchPath.test(new URL(r.url()).pathname) && r.request().method() === "POST");
  await page.getByRole("button", { name: "Search flights", exact: true }).click();
  await expect(page.locator("#request-progress")).toBeVisible();
  await expect(page.locator("#request-label")).toHaveText("Finding flight routes");
  await expect(page.locator("#request-progress progress")).not.toHaveAttribute("value");
  await expect(page.locator("#map-loading")).toBeVisible();
  await expect(page.locator("[data-edit]").first()).toBeDisabled();
  await expect(page.locator("#connection-editor")).toBeHidden();
  await expect(page.locator("#request-elapsed")).not.toHaveText("0s");
  await expect(page.locator("[data-option]").first()).toBeVisible();
  release(); await pending;
  await expect(page.locator("#map-loading")).toBeHidden();
  await expect(page.locator("#request-progress")).toBeHidden();
  await expect(page.locator("[data-edit]").first()).toBeEnabled();
});

check("failed searches clear loading activity and retain the previous journey", async (page) => {
  await open(page);
  const before = identity((await exported(page)).selection.legs);
  await page.route("**/v1/itineraries/search/local", route => route.fulfill({status:503,json:{detail:{message:"Search temporarily unavailable"}}}));
  await page.getByRole("button", { name: "Search flights", exact: true }).click();
  await expect(page.locator("#error")).toBeVisible();
  await expect(page.locator("#map-loading")).toBeHidden();
  await expect(page.locator("#request-progress")).toBeHidden();
  assert.deepEqual(identity((await exported(page)).selection.legs), before);
  await expect(page.locator("[data-edit]").first()).toBeEnabled();
});

check("a replacement search cancels an in-flight connection lookup", async (page) => {
  await open(page);
  let release, started, completed;
  const gate = new Promise(resolve => { release = resolve; });
  const incoming = new Promise(resolve => { started = resolve; });
  const finished = new Promise(resolve => { completed = resolve; });
  await page.route("**/v1/plans/rebuild", async route => {
    started();
    await gate;
    try { await route.continue(); } catch { /* The replacement search aborts this read. */ }
    finally { completed(); }
  });
  await edit(page);
  await page.locator("#find-next").click();
  await incoming;
  const replacement = await search(page);
  release();
  await finished;
  await expect(page.locator("#connection-editor")).toBeHidden();
  await expect(page.locator("#next-options")).toBeEmpty();
  assert.deepEqual(identity((await exported(page)).selection.legs), identity(replacement.items[0].legs));
  await edit(page);
  await expect(page.locator("#next-options")).toBeEmpty();
  await expect(page.locator("#find-next")).toBeEnabled();
});

check("compact search leads a results-first workspace with a selected journey canvas", async (page) => {
  await open(page);
  await expect(page.locator("#route-map .map-land")).toHaveCount(1);
  const map = await page.locator(".map-panel").boundingBox(),
    form = await page.locator(".search-section").boundingBox(),
    options = await page.locator(".options-panel").boundingBox();
  assert.ok(options.y >= form.y + form.height);
  assert.ok(map.y >= form.y + form.height);
  assert.ok(map.x >= options.x + options.width);
  assert.equal(await page.locator("#search-details").evaluate((details) => details.open), false);
  await expect(page.getByLabel("Maximum connection (days)")).toBeVisible();
  await expect(page.locator(".option-timeline").first()).toBeVisible();
  await expect(page.locator(".option-comparison").first()).toBeVisible();
  await expect(page.locator(".option-facts").first()).toBeVisible();
  await expect(page.locator("#route-mode")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#journey-summary")).toContainText("Total journey");
  await page.emulateMedia({reducedMotion:"reduce",colorScheme:"dark"});
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), "light");
  await expect(page.locator("#route-map")).toBeVisible();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth > innerWidth), false);
});

check("globe fit keeps a distant origin visible beside clustered connection airports", async (page) => {
  await page.route("**/v1/itineraries/search/local", async route => {
    const response = await route.fetch(), data = await response.json();
    const coordinates = {YYZ:[-75,43],LHR:[120,25],CDG:[100,14]};
    data.airports = data.airports.map(airport => coordinates[airport.iata]
      ? {...airport,longitude:coordinates[airport.iata][0],latitude:coordinates[airport.iata][1]} : airport);
    await route.fulfill({response,json:data});
  });
  await open(page);
  await openMapControls(page);
  await page.getByRole("button", { name: "Earth", exact: true }).click();
  for (const code of ["YYZ", "LHR", "CDG"])
    await expect(page.locator("#route-map .map-airport-label", {hasText:code})).toBeVisible();
  await page.getByRole("button", {name:"Fit journey",exact:true}).click();
  await expect(page.locator("#route-map .map-airport-label", {hasText:"YYZ"})).toBeVisible();
});


check("fresh visits keep airports blank and do not search automatically", async (page) => {
  const calls = [];
  page.on("request", (request) => {
    if (searchPath.test(new URL(request.url()).pathname) || request.url().includes("/v1/search-availability?")) calls.push(request.url());
  });
  const bootstrap = page.waitForResponse("**/v1/bootstrap");
  await page.goto(base);
  await bootstrap;
  await expect(page.getByLabel("From", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("To", { exact: true })).toHaveValue("");
  await expect(page.locator("#date")).toBeDisabled();
  await expect(page.locator("#search-button")).toBeDisabled();
  assert.deepEqual(calls, []);
  await expect(page.locator('button[data-view="journey"]')).toBeDisabled();
  await expect(page.locator('button[data-view="map"]')).toBeEnabled();
});

check("date availability keeps Journey disabled until a route is selected", async (page) => {
  const bootstrap = page.waitForResponse("**/v1/bootstrap");
  await page.goto(base);
  await bootstrap;
  await page.getByLabel("From", {exact:true}).fill("YYZ");
  const available = page.waitForResponse("**/v1/search-availability?*");
  await page.getByLabel("To", {exact:true}).fill("CDG");
  await available;
  await expect(page.locator("#search-button")).toBeEnabled();
  await expect(page.locator('button[data-view="journey"]')).toBeDisabled();
  await expect(page.locator('button[data-view="map"]')).toBeEnabled();
  const data = await search(page);
  assert.ok(data.items.length);
  await expect(page.locator('button[data-view="journey"]')).toBeEnabled();
});

check("Earth survives reload and remains available before and after an empty search", async (page) => {
  await open(page);
  await page.locator("#globe-mode").click();
  await result(page, searchPath, () => page.reload());
  await expect(page.locator("#globe-mode")).toHaveAttribute("aria-pressed", "true");
  await page.goto(base);
  await expect(page.locator("#globe-mode")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#route-map .map-land")).toBeAttached();
  await expect(page.locator(".map-panel")).toBeVisible();
  await open(page);
  await page.getByLabel("Maximum connection (days)").fill("0.05");
  const empty = await search(page);
  assert.equal(empty.items.length, 0);
  await expect(page.locator("#options")).toContainText("No matching route yet");
  await expect(page.locator(".map-panel")).toBeVisible();
  await page.getByRole("button", {name:"Review preferences",exact:true}).click();
  await expect(page.locator("#search-details")).toHaveAttribute("open", "");
  await page.keyboard.press("Escape");
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('[data-view="map"]').filter({hasText:"Map"})).toBeEnabled();
  await page.getByRole("button", {name:"Map",exact:true}).click();
  await expect(page.locator(".map-panel")).toBeVisible();
  await expect(page.locator("#globe-mode")).toHaveAttribute("aria-pressed", "true");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
});

check("airport lookup supports names keyboard selection clearing and swapping", async (page) => {
  await page.goto(base);
  const origin = page.getByLabel("From", { exact: true }), destination = page.getByLabel("To", { exact: true });
  const lookup = page.waitForResponse((r) => new URL(r.url()).pathname === "/v1/airports");
  await origin.fill("Heathrow");
  await lookup;
  await expect(page.locator('#origin-suggestions [data-airport="LHR"]')).toBeVisible();
  await origin.press("ArrowDown");
  await expect(origin).toHaveAttribute("aria-activedescendant", /origin-suggestions-\d+/);
  await origin.press("Enter");
  await expect(origin).toHaveValue("LHR");
  await expect(page.locator("#origin-name")).toContainText("Heathrow");
  await expect(page.locator("#origin-suggestions")).toBeHidden();
  await destination.fill("NRT");
  await page.getByRole("button", { name: "Swap origin and destination" }).click();
  await expect(origin).toHaveValue("NRT");
  await expect(destination).toHaveValue("LHR");
  await page.getByRole("button", { name: "Clear from airport" }).click();
  await expect(origin).toHaveValue("");
  await expect(origin).toBeFocused();
  await expect(page.locator("#date")).toBeDisabled();
  await expect(page.locator("#search-button")).toBeDisabled();
});

check("phone airport suggestions are tappable and fit the screen", async (page) => {
  await page.goto(base);
  const input = page.getByLabel("From", { exact: true });
  await input.fill("Heathrow");
  const option = page.locator('#origin-suggestions [data-airport="LHR"]');
  await expect(option).toBeVisible();
  await option.tap();
  await expect(input).toHaveValue("LHR");
  await expect(page.locator("#origin-suggestions")).toBeHidden();
  await input.tap();
  await expect(page.locator("#origin-suggestions")).toBeVisible();
  await page.getByLabel("To", { exact: true }).tap();
  await expect(page.locator("#origin-suggestions")).toBeHidden();
  const bounds = await input.boundingBox();
  assert.ok(bounds.width > 180, "phone airport inputs have room for names");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}, { viewport: { width: 320, height: 800 }, isMobile: true, hasTouch: true });

check("airport lookup ignores stale replies and permits codes during lookup failures", async (page) => {
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const intercepted = new Promise(resolve => { started = resolve; });
  await page.route("**/v1/airports?*", async route => {
    const q = new URL(route.request().url()).searchParams.get("q");
    if (q === "Heathrow") {
      started(); await gate;
      await route.fulfill({ json: [{ iata: "LHR", name: "Heathrow" }] });
    } else await route.fulfill({ status: 503, json: { detail: "Unavailable" } });
  });
  try {
    await page.goto(base);
    const input = page.getByLabel("From", { exact: true });
    await input.fill("Heathrow");
    await intercepted;
    await input.fill("YYZ");
    await expect(page.locator(".airport-picker-status").first()).toContainText("lookup unavailable");
    release();
    await expect(input).toHaveValue("YYZ");
    await expect(page.locator('#origin-suggestions [data-airport="LHR"]')).toHaveCount(0);
    await page.getByLabel("To", { exact: true }).fill("CDG");
    await expect(page.locator("#search-button")).toBeEnabled();
  } finally { release(); }
});

check("airport lookup supports local searches without bootstrap airport metadata", async (page) => {
  await page.route("**/v1/bootstrap", async route => {
    const response = await route.fetch(), data = await response.json();
    await route.fulfill({ response, json: { ...data, airports: [] } });
  });
  await page.goto(base);
  await page.getByLabel("From", { exact: true }).fill("YYZ");
  await page.getByLabel("To", { exact: true }).fill("CDG");
  await expect(page.locator("#search-button")).toBeEnabled();
  const data = await search(page);
  assert.equal(data.departure_timezone, "America/Toronto");
  assert.ok(data.items.length);
});

check("delayed bootstrap selects available flight times without replacing typed airports", async page => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route("**/v1/bootstrap", async route => {
    const response = await route.fetch(), data = await response.json();
    await gate;
    await route.fulfill({ response, json: { ...data, default_time_basis: "observed", available_time_bases: ["observed"] } });
  });
  await page.route("**/v1/search-availability?*", route => fulfillAvailability(route, {
    timezone: "America/Toronto", dates: ["2026-10-17"],
  }));
  try {
    await page.goto(base);
    await page.getByLabel("From", { exact: true }).fill("YYZ");
    await page.getByLabel("To", { exact: true }).fill("NRT");
    release();
    await expect(page.getByLabel("Flight times")).toHaveValue("observed");
    await expect(page.getByLabel("From", { exact: true })).toHaveValue("YYZ");
    await expect(page.getByLabel("To", { exact: true })).toHaveValue("NRT");
    await expect(page.locator("#date-availability")).toContainText("Observed historical legs");
    await expect(page.locator("[data-option]")).toHaveCount(0);
  } finally { release(); }
});
