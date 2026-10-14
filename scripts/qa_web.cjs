// Behavior checks against the real demo API. No saved output or screenshot baselines.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { test, before, after } = require("node:test");
const { chromium } = require("playwright");
const { expect } = require("playwright/test");
const base = process.env.FLYJI_QA_URL || "http://127.0.0.1:8013";
const out = process.env.FLYJI_QA_OUTPUT || path.resolve("var/web-qa");
const searchPath = /\/v1\/itineraries\/search(?:\/local)?$/;
const identity = (legs) =>
  legs.map(({ provider, provider_id }) => ({ provider, provider_id }));
let browser;
before(async () => {
  browser = await chromium.launch({
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
  await action();
  const response = await pending;
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}
async function open(page, routes = true) {
  const data = await result(page, searchPath, () => page.goto(base));
  await expect(
    page.getByRole("button", { name: "Search flights", exact: true }),
  ).toBeEnabled();
  if (routes) await expect(page.locator("[data-option]").first()).toBeVisible();
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
  return data;
}
async function exported(page) {
  const pending = page.waitForEvent("download");
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
    await expect(page.locator("#date-zones")).toContainText("7 days");
    assert.equal(initial.query.departure_start, "2026-10-17T04:00:00Z");
    const checkWindow = (query) =>
      assert.equal(
        Date.parse(query.horizon_end) - Date.parse(query.departure_end),
        7 * 24 * 60 * 60 * 1000,
      );
    checkWindow(initial.query);
    assert.equal(new URL(page.url()).searchParams.has("arrival"), false);
    assert.equal(new URL(page.url()).searchParams.has("arrival-fold"), false);

    await page.locator("#search-details > summary").click();
    await page
      .getByLabel("Last departure date", { exact: true })
      .fill("2026-10-19");
    const ranged = await search(page);
    assert.equal(ranged.query.departure_end, "2026-10-20T04:00:00Z");
    checkWindow(ranged.query);
    const saved = await exported(page);
    assert.equal(saved.query_inputs["last-date"], "2026-10-19");
    assert.equal(Object.hasOwn(saved.query_inputs, "arrival"), false);
    assert.equal(Object.hasOwn(saved.query_inputs, "arrival-fold"), false);
    assert.equal(saved.query.horizon_end, ranged.query.horizon_end);
    await page
      .getByRole("button", { name: "Copy search link", exact: true })
      .click();
    await expect(page.locator("#status")).toContainText("Search link copied");
    const copied = new URL(
      await page.evaluate(() => navigator.clipboard.readText()),
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

    await page.getByLabel("Input clock").selectOption("UTC");
    const utc = await search(page);
    assert.equal(utc.query.departure_start, "2026-10-17T00:00:00Z");
    assert.equal(utc.query.departure_end, "2026-10-20T00:00:00Z");
    checkWindow(utc.query);
    for (const { path, body } of submitted.filter(({ path }) => searchPath.test(path))) {
      assert.ok(
        !Object.keys(body).some((key) => key.startsWith("arrival_")),
        path,
      );
    }
  },
  { permissions: ["clipboard-read", "clipboard-write"] },
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
    assert.ok(selected.selection.diagnostics.snapshot_id);
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
    const plan = await next(page, () =>
      page.getByLabel("Next airport", { exact: true }).press("Enter"),
    );
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
    await page.getByRole("button", { name: "Undo edit", exact: true }).click();
    assert.deepEqual(
      identity((await exported(page)).selection.legs),
      identity(before.legs),
    );
    await expect(
      page.getByRole("button", { name: "Undo edit", exact: true }),
    ).toBeDisabled();
  },
);

check("ordered via lists survive typing, links and export", async (page, context) => {
  await open(page);
  const via = page.getByLabel("Via airports", { exact: true });
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
  await via.fill("LHR, A");
  const appended = await page
    .locator("#via-suggestions [role=option]")
    .evaluateAll((options) => options.map((o) => o.dataset.value));
  assert.ok(appended.includes("LHR, AMS"));
  assert.ok(!appended.includes("LHR, LHR"));
  await via.press("ArrowDown");
  await expect(via).toHaveAttribute("aria-activedescendant", "via-option-0");
  await via.press("Enter");
  await expect(via).toHaveValue("LHR, AMS");
  await expect(via).toBeFocused();
  await expect(via).toHaveAttribute("aria-expanded", "false");
  await via.fill("LHR, LG, KEF");
  await via.evaluate((input) => input.setSelectionRange(6, 6));
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
  await via.fill("LHR, ");
  const choices = page.locator("#via-suggestions [role=option]");
  assert.ok(await choices.count() > 1);
  await via.press("ArrowUp");
  await expect(via).toHaveAttribute("aria-activedescendant", await choices.last().getAttribute("id"));
  await via.press("ArrowDown");
  await expect(via).toHaveAttribute("aria-activedescendant", await choices.first().getAttribute("id"));
  await via.press("Escape");
  await expect(via).toHaveValue("LHR, ");
  await expect(via).toHaveAttribute("aria-expanded", "false");
  await via.press("ArrowDown");
  await via.press("Tab");
  await expect(via).not.toBeFocused();
  await expect(via).toHaveValue("LHR, ");
  await expect(via).toHaveAttribute("aria-expanded", "false");
  for (const [value, error] of [
    ["LHR, lhr", "must not repeat"],
    ["LHR, FRA1", "Invalid airport code: FRA1"],
    ["YYZ, AMS", "endpoint"],
  ]) {
    await via.fill(value);
    await via.press("Enter");
    await expect(page.locator("#error")).toContainText(error);
    assert.deepEqual(
      identity((await exported(page)).selection.legs),
      identity(before.legs),
    );
  }
  await page.locator("#search-details > summary").click();
  await page.locator("#search-form [name=stops]").selectOption("3");
  await via.fill("LHR, AMS, KEF, YUL");
  await via.press("Enter");
  await expect(page.locator("#error")).toContainText("Use fewer codes or allow permitted airport changes");
  assert.deepEqual(
    identity((await exported(page)).selection.legs),
    identity(before.legs),
  );
});

check(
  "connection bounds and avoided airports reach the engine",
  async (page) => {
    await open(page);
    await page.locator("#search-details > summary").click();
    await page
      .locator("#search-form")
      .getByLabel("Minimum connection (minutes)")
      .fill("120");
    await page.getByLabel("Maximum connection").selectOption("any");
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
  await page.getByLabel("To", { exact: true }).fill("ABC");
  await page.getByLabel("Input clock").selectOption("UTC");
  const empty = await search(page);
  assert.equal(empty.diagnostics.exhaustive, true);
  assert.deepEqual(empty.items, []);
  await expect(page.locator("#partial")).toBeHidden();
  await expect(page.locator("[data-option]")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Export JSON", exact: true }),
  ).toBeDisabled();
});

check("storage failure can be retried", async (page) => {
  let fail = true;
  await page.route("**/v1/catalog", (route) =>
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
  await page.goto(base);
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
    await page.route("**/v1/catalog", async (route) => {
      const response = await route.fetch(),
        data = await response.json();
      await route.fulfill({ response, json: { ...data, airports: [] } });
    });
    await page.goto(base);
    await expect(
      page.getByRole("button", { name: "Search flights", exact: true }),
    ).toBeEnabled();
    await page.getByLabel("From", { exact: true }).fill("yyz");
    await page.getByLabel("To", { exact: true }).fill("cdg");
    await page.getByLabel("Input clock").selectOption("UTC");
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
    await page.getByRole("button", { name: "Map", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Map", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "Globe", exact: true }).click();
    const mapImage = page.getByRole("img", { name: /flight route|world map/i });
    await expect(mapImage.locator("path").first()).toBeAttached();
    const cameraBefore = await mapImage.screenshot();
    await page
      .getByRole("button", { name: "Tilt globe up", exact: true })
      .click();
    await expect
      .poll(async () => !(await mapImage.screenshot()).equals(cameraBefore))
      .toBe(true);
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page
      .getByRole("button", { name: "Fit journey", exact: true })
      .click();
    await page.getByLabel("Schedule time", { exact: false }).fill("500");
    const cursor = await page
      .getByLabel("Schedule time", { exact: false })
      .inputValue();
    await page.getByLabel("Leg times").selectOption("UTC");
    assert.deepEqual(identity((await exported(page)).selection.legs), before);
    assert.equal(
      await page.getByLabel("Schedule time", { exact: false }).inputValue(),
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
    const via = page.getByLabel("Via airports", { exact: true });
    await via.fill("LHR, A");
    await page.getByRole("option", { name: /^AMS / }).tap();
    await expect(via).toHaveValue("LHR, AMS");
    await expect(via).toHaveAttribute("aria-expanded", "false");
    const views = page.getByRole("navigation", { name: "Workspace view" });
    for (const width of [320, 390, 736, 1180]) {
      await page.setViewportSize({ width, height: 900 });
      await via.fill("LHR, ");
      await expect(via).toHaveAttribute("aria-expanded", "true");
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await via.press("Escape");
      if (await views.isVisible()) {
        await views.getByRole("button", { name: "Journey", exact: true }).tap();
        await expect(
          page.getByRole("button", { name: "Export JSON", exact: true }),
        ).toBeVisible();
        await views.getByRole("button", { name: "Map", exact: true }).tap();
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
    const threshold = Date.parse(first.arrival_scheduled) + 12 * 60 * 60 * 1000;
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

check("stale timezone metadata is corrected before retry", async (page) => {
  let actualZone, departureDate;
  await page.route("**/v1/catalog", async (route) => {
    const response = await route.fetch(),
      catalog = await response.json();
    const origin = catalog.airports.find((a) => a.iata === "YYZ");
    actualZone = origin.timezone;
    const { localDate } = await import("../src/server/web/format.mjs");
    departureDate = localDate(catalog.dataset.first_departure, actualZone);
    origin.timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: catalog });
  });
  const pending = page.waitForResponse((r) =>
    searchPath.test(new URL(r.url()).pathname),
  );
  await page.goto(base);
  const failed = await pending;
  assert.equal(failed.status(), 422);
  assert.equal((await failed.json()).detail.code, "airport_timezone_changed");
  await expect(page.locator("#error")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Search flights", exact: true }),
  ).toBeEnabled();
  // The error asks the traveler to review dates; retain explicit input rather
  // than silently shifting it when corrected metadata changes the clock.
  await page.getByLabel("Departure date", { exact: true }).fill(departureDate);
  const retried = await search(page);
  assert.equal(retried.departure_timezone, actualZone);
  assert.ok(retried.items.length);
  await expect(page.locator("#error")).toBeHidden();
});

check(
  "map failure leaves flight inspection and editing available",
  async (page) => {
    await page.route("**/assets/vendor/world-110m.geojson", (route) =>
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
