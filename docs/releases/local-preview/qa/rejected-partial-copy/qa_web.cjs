// Browser QA for the served product. Requires Playwright only in the QA environment.
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto");
const { chromium } = require("playwright");
const base = process.env.FLYJI_QA_URL || "http://127.0.0.1:8013";
const out = process.env.FLYJI_QA_OUTPUT || path.resolve("var/web-qa");
const checks = [],
  errors = [],
  network = [],
  expectedHTTPConsole = [];
const sha = (p) =>
  crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
function ensure(value, message) {
  if (!value) throw Error(message);
  checks.push(message);
  console.log("PASS", message);
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.FLYJI_QA_BROWSER === "chromium"
      ? {}
      : { channel: "chrome" }),
  });
  const page = await browser.newPage({
    viewport: { width: 1180, height: 1000 },
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("request", (request) => network.push(request.url()));
  page.setDefaultTimeout(8000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const shot = async (name) =>
    page.screenshot({ path: path.join(out, name), fullPage: true });
  await page.goto(base);
  await page.locator("[data-option]").first().waitFor();
  await page.locator(".map-land").first().waitFor({ state: "attached" });
  ensure(
    (await page.locator("[data-option][aria-pressed=true]").count()) === 1,
    "Exactly one dated journey selected",
  );
  ensure(
    (await page.locator(".map-land").count()) === 177,
    "Published basemap renders from local assets",
  );
  ensure(
    await page
      .locator(".map-land")
      .evaluateAll((paths) =>
        paths.some((p) => p.getAttribute("d")?.length > 40),
      ),
    "Visible globe countries have projected geometry",
  );
  ensure(
    (await page.locator(".map-flight").count()) > 0,
    "Selected journey is drawn on the globe",
  );
  ensure(
    (await page.locator("#dataset-tag").textContent()) ===
      "Demo schedules · 12 records",
    "Synthetic data is identified",
  );
  ensure(
    (await page.locator("#date-zones").textContent()).includes(
      "America/Toronto",
    ),
    "Airport-local input clock is explicit",
  );
  await shot("desktop-initial.png");
  const target = page
    .getByRole("button", { name: /YYZ → LHR → AMS → CDG/ })
    .first();
  await target.click();
  ensure(
    (await page.locator(".leg").count()) === 3,
    "Select exact three-leg journey from actual API results",
  );
  await page.locator('[data-edit="0"]').click();
  await page.locator("#connection-form [name=window]").selectOption("any");
  await page.locator("#connection-form [name=airport-change]").check();
  await page.locator("#find-next").click();
  await page.locator("[data-next]").first().waitFor();
  ensure(
    (await page.locator("#next-options").innerText()).includes(
      "No complete onward route in loaded data",
    ),
    "Dead-end choices remain explicit",
  );
  await shot("desktop-editor.png");
  const overnight = page
    .locator(".next-option")
    .filter({ hasText: /19 Oct/ })
    .filter({ hasText: /LGW → AMS/ })
    .first();
  await overnight.getByRole("button", { name: "Apply flight" }).click();
  await page.waitForFunction(
    () => document.getElementById("connection-editor").hidden,
  );
  ensure(
    (await page.locator(".leg").nth(1).getAttribute("data-provider-id")) ===
      "lgw-ams-overnight",
    "Explicit overnight next leg retained",
  );
  ensure(
    (await page.locator(".leg").nth(2).getAttribute("data-provider-id")) ===
      "ams-cdg-next-day",
    "All later legs recalculated",
  );
  ensure(
    (await page.locator(".map-transfer").count()) === 1,
    "Airport change has distinct ground segment",
  );
  await page.locator("#time-cursor").fill("500");
  ensure(
    (await page.locator("#cursor-detail").textContent()).includes(
      "Airport change LHR → LGW",
    ),
    "Ground-transfer gap does not claim arrival at the next airport",
  );
  ensure(
    (await page.locator(".map-position").count()) === 0,
    "Schedule does not invent a location during an airport change",
  );
  ensure(
    (await page.locator(".leg-time").first().textContent()).includes("2026"),
    "Full flight timestamps include the year",
  );
  await shot("desktop-overnight.png");
  await page.locator("#undo").click();
  ensure(
    (await page.locator(".leg").nth(1).getAttribute("data-provider-id")) ===
      "lhr-ams-early",
    "Undo restores exact prior dated sequence",
  );
  await page.locator("#time-cursor").fill("500");
  ensure(
    (await page.locator("#cursor-detail").textContent()).includes(
      "schedule illustration",
    ),
    "Schedule cursor is labelled as an illustration",
  );
  await page.locator("#flat-mode").click();
  ensure(
    (await page.locator("#flat-mode").getAttribute("aria-pressed")) === "true",
    "Flat map remains available",
  );
  ensure(
    (await page.locator("#tilt-up").isDisabled()) &&
      (await page.locator("#zoom-in").isDisabled()),
    "Globe camera controls are disabled on the flat map",
  );
  await page.locator('[data-focus="0"]').click();
  ensure(
    (await page.locator("#globe-mode").getAttribute("aria-pressed")) === "true",
    "Focus a leg recovers the globe",
  );
  const camera = () =>
    page
      .locator(".map-land")
      .evaluateAll((nodes) =>
        JSON.stringify(nodes.map((n) => n.getAttribute("d"))),
      );
  const initialCamera = await camera();
  await page.locator("#tilt-up").click();
  ensure(
    (await camera()) !== initialCamera,
    "Tilt button changes projected geography",
  );
  const tilted = await camera();
  await page.locator("#zoom-in").click();
  ensure((await camera()) !== tilted, "Zoom button changes map scale");
  await page.locator("#fit-map").click();
  const mapPaths = await page
    .locator(".map-land")
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("d")));
  await page.locator("#time-cursor").fill("600");
  ensure(
    JSON.stringify(
      await page
        .locator(".map-land")
        .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("d"))),
    ) === JSON.stringify(mapPaths),
    "Scrubbing preserves projected basemap paths",
  );
  await page.evaluate(() => {
    window.qaLandNode = document.querySelector(".map-land");
  });
  await page.locator("#time-cursor").fill("650");
  ensure(
    await page.evaluate(
      () => window.qaLandNode === document.querySelector(".map-land"),
    ),
    "Scrubbing reuses basemap elements rather than rebuilding geometry",
  );
  const ids = () =>
    page
      .locator(".leg")
      .evaluateAll((nodes) => nodes.map((n) => n.dataset.providerId));
  const selectedIds = await ids(),
    cursor = await page.locator("#time-cursor").inputValue();
  await page.locator("#display-clock").selectOption("UTC");
  ensure(
    JSON.stringify(await ids()) === JSON.stringify(selectedIds),
    "Display clock preserves dated flight identities",
  );
  ensure(
    (await page.locator("#time-cursor").inputValue()) === cursor,
    "Display clock preserves cursor",
  );
  await page.locator("#display-clock").selectOption("local");
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export").click();
  const download = await downloadPromise;
  await download.saveAs(path.join(out, "journey-export.json"));
  const exported = JSON.parse(
    fs.readFileSync(path.join(out, "journey-export.json"), "utf8"),
  );
  ensure(
    JSON.stringify(exported.selection.legs.map((f) => f.provider_id)) ===
      JSON.stringify(selectedIds),
    "Export retains exact selected dated records",
  );
  ensure(
    exported.coverage === "unknown" &&
      exported.selection.diagnostics.snapshot_id,
    "Export carries coverage and snapshot evidence",
  );
  for (const width of [320, 390, 736, 1024, 1510]) {
    await page.setViewportSize({ width, height: 1000 });
    if (width < 721)
      await page.locator(".mobile-views [data-view=map]").click();
    await page.waitForTimeout(100);
    ensure(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `${width}px: no horizontal page overflow`,
    );
    ensure(
      await page
        .locator("#route-map")
        .evaluate(
          (el) => Math.abs(el.clientWidth - el.viewBox.baseVal.width) < 1,
        ),
      `${width}px: projection uses measured width`,
    );
    if (width === 390) {
      await shot("mobile-map.png");
      await page.locator(".mobile-views [data-view=journey]").click();
      await shot("mobile-journey.png");
      await page.locator(".mobile-views [data-view=map]").click();
    }
    if (width < 721)
      ensure(
        await page.locator(".options-panel").isHidden(),
        "Mobile map view avoids scrolling past the results",
      );
  }
  await page.setViewportSize({ width: 1180, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  await shot("desktop-dark.png");
  ensure(
    !network.some((url) => !url.startsWith(base)),
    "No external runtime requests",
  );
  ensure(
    errors.length === 0,
    "No browser runtime or CSP errors in normal flows",
  );

  // Use isolated pages for fault injection; API responses still come from the real engine.
  async function fresh(options = {}) {
    const context = await browser.newContext({
      viewport: { width: 1180, height: 1000 },
      ...options,
    });
    const p = await context.newPage();
    p.setDefaultTimeout(8000);
    p.on("pageerror", (error) => errors.push(error.message));
    p.on("request", (request) => network.push(request.url()));
    p.on("console", (msg) => {
      if (msg.type() === "error") expectedHTTPConsole.push(msg.text());
    });
    return { context, p };
  }
  let { context, p } = await fresh();
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator("#search-details").evaluate((el) => (el.open = true));
  await p.locator("#search-form [name=minimum]").fill("120");
  await p.locator("#search-form [name=maximum]").selectOption("any");
  await p.locator("#search-form [name=avoid]").fill("AMS");
  const constrained = p.waitForResponse(
    (response) =>
      response.url().endsWith("/v1/itineraries/search/local") &&
      response.request().method() === "POST",
  );
  await p.locator("#search-button").click();
  const constrainedData = await (await constrained).json();
  await p.waitForFunction(
    () => document.getElementById("search-button").disabled === false,
  );
  ensure(
    constrainedData.query.minimum_connection_minutes === 120 &&
      constrainedData.query.maximum_connection_minutes === null,
    "Browser sends actual connection constraints",
  );
  ensure(
    constrainedData.query.avoid_airports[0] === "AMS" &&
      constrainedData.items.every((item) =>
        item.legs.every((f) => f.destination_iata !== "AMS"),
      ),
    "Avoided airport filter is enforced by actual engine",
  );
  await p.locator('[data-edit="0"]').click();
  await p.locator("#connection-form [name=window]").selectOption("any");
  await p.locator("#find-next").click();
  await p.locator("[data-next]").first().waitFor();
  ensure(
    !(await p.locator("#next-options").innerText()).includes("→ AMS"),
    "Connection editing retains avoided-airport filter",
  );
  await p.locator("#close-editor").click();
  const link = p.url();
  const reloaded = await context.newPage();
  await reloaded.goto(link);
  await reloaded.locator("[data-option]").first().waitFor();
  ensure(
    (await reloaded.locator("#search-form [name=minimum]").inputValue()) ===
      "120" &&
      (await reloaded.locator("#search-form [name=avoid]").inputValue()) ===
        "AMS",
    "Search URL restores canonical constraints",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p
    .getByRole("button", { name: /YYZ → LHR → AMS → CDG/ })
    .first()
    .click();
  await p.locator('[data-edit="0"]').click();
  await p.locator("#connection-form [name=window]").selectOption("after");
  await p.locator("#connection-form [name=after]").fill("2026-10-19T00:00");
  await p.locator("#connection-form [name=airport-change]").check();
  const resolve = p.waitForResponse((response) =>
    response.url().endsWith("/v1/time/resolve"),
  );
  await p.locator("#find-next").click();
  const resolved = await (await resolve).json();
  await p.locator("[data-next]").first().waitFor();
  ensure(
    resolved.utc === "2026-10-18T23:00:00Z",
    "Not-before local time resolves through actual airport zone endpoint",
  );
  ensure(
    !(await p.locator("#next-options").innerText()).includes("18 Oct"),
    "Not-before window excludes earlier next legs",
  );
  await p.locator("#close-editor").click();
  await p.locator('[data-edit="0"]').click();
  ensure(
    await p
      .locator("#connection-form [name=after]")
      .evaluate((el) => !el.required),
    "Reopening editor clears hidden required-date constraint",
  );
  await p.locator("#find-next").click();
  await p.locator("[data-next]").first().waitFor();
  const before = await p
    .locator(".leg")
    .evaluateAll((nodes) => nodes.map((n) => n.dataset.providerId));
  await p.route("**/v1/plans/rebuild", (route) =>
    route.request().postDataJSON().chosen_next
      ? route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              code: "prefix_cancelled",
              message:
                "A selected flight was cancelled. Choose another flight.",
            },
          }),
        })
      : route.continue(),
  );
  await p.locator('[data-next="0"]').click();
  await p.locator("#editor-error").waitFor();
  ensure(
    JSON.stringify(
      await p
        .locator(".leg")
        .evaluateAll((nodes) => nodes.map((n) => n.dataset.providerId)),
    ) === JSON.stringify(before),
    "Rejected stale choice preserves previous journey",
  );
  await p.screenshot({
    path: path.join(out, "selection-error.png"),
    fullPage: true,
  });
  await context.close();

  ({ context, p } = await fresh());
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  const winner = await p.evaluate(async () => {
    const { RequestLane } = await import("/assets/format.mjs"),
      original = window.fetch,
      seen = [];
    window.fetch = async (url) => {
      await new Promise((resolve) =>
        setTimeout(resolve, url === "slow" ? 80 : 5),
      );
      return { ok: true, json: async () => url };
    };
    try {
      const lane = new RequestLane();
      await Promise.all([
        lane.run(
          "slow",
          {},
          (result) => seen.push(result),
          () => {},
        ),
        lane.run(
          "fast",
          {},
          (result) => seen.push(result),
          () => {},
        ),
      ]);
      return seen;
    } finally {
      window.fetch = original;
    }
  });
  ensure(
    JSON.stringify(winner) === '["fast"]',
    "Latest request wins when transport ignores cancellation",
  );
  await p.locator("#search-details").evaluate((el) => (el.open = true));
  await p.locator("[name=input-clock]").selectOption("UTC");
  const utcResponse = p.waitForResponse((response) =>
    response.url().endsWith("/v1/itineraries/search"),
  );
  await p.locator("#search-button").click();
  const utcData = await (await utcResponse).json();
  ensure(
    utcData.query.departure_start === "2026-10-17T00:00:00Z",
    "UTC input is interpreted independently of browser timezone",
  );
  await p.waitForFunction(
    () => !document.getElementById("search-button").disabled,
  );
  await p.locator("[name=destination]").fill("KEF");
  await p.locator("#search-button").click();
  await p
    .getByText("No matching route in loaded data.", { exact: false })
    .waitFor();
  ensure(
    (await p.locator(".leg").count()) === 0,
    "Empty search clears selected journey without inventing a route",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/itineraries/search/local", async (route) => {
    const body = route.request().postDataJSON();
    const response = await route.fetch({
      postData: JSON.stringify({ ...body, limits: { max_candidates: 1 } }),
    });
    await route.fulfill({ response });
  });
  await p.goto(base);
  await p.locator("#partial").waitFor();
  ensure(
    (await p.locator("#partial").textContent()).includes(
      "ranking is incomplete",
    ),
    "Actual bounded search is visibly partial",
  );
  ensure(
    (await p.locator("#options").textContent()).includes(
      "search stopped early",
    ),
    "Partial empty result does not claim no route",
  );
  await p.screenshot({
    path: path.join(out, "partial-search.png"),
    fullPage: true,
  });
  await context.close();

  ({ context, p } = await fresh());
  let fail = true;
  await p.route("**/v1/catalog", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              code: "storage_unavailable",
              message:
                "Flight data is unavailable. Initialize the local database, then retry.",
            },
          }),
        })
      : route.continue(),
  );
  await p.goto(base);
  await p.locator("#retry-catalog").waitFor();
  ensure(
    await p.locator("#error").isVisible(),
    "Unavailable storage has a visible recovery action",
  );
  fail = false;
  await p.locator("#retry-catalog").click();
  await p.locator("[data-option]").first().waitFor();
  ensure(
    await p.locator("#error").isHidden(),
    "Retry recovers the real data connection",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/catalog", async (route) => {
    const response = await route.fetch(),
      catalog = await response.json();
    catalog.airports.find((a) => a.iata === "YYZ").timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: catalog });
  });
  await p.goto(base + "/?date=2026-10-17");
  await p.locator("#error").waitFor();
  ensure(
    (await p.locator("#error").textContent()).includes("timezone changed"),
    "Stale timezone reference fails visibly instead of reinterpreting local dates",
  );
  ensure(
    (await p.locator("#date-zones").textContent()).includes("America/Toronto"),
    "Timezone mismatch updates the declared input reference",
  );
  await p.locator("#search-button").click();
  await p.locator("[data-option]").first().waitFor();
  ensure(
    (await p.locator(".leg-time").first().textContent()).includes("GMT-4"),
    "Retry uses returned snapshot metadata for leg clocks",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/assets/vendor/world-110m.geojson", (route) =>
    route.abort(),
  );
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator("#map-error").waitFor();
  ensure(
    (await p.locator(".leg").count()) > 0,
    "Map failure leaves flight facts available",
  );
  await p.locator('[data-edit="0"]').click();
  await p.locator("#find-next").click();
  await p.locator("[data-next]").first().waitFor();
  ensure(
    (await p.locator("[data-next]").count()) > 0,
    "Map failure leaves connection editing available",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/assets/vendor/d3*", (route) => route.abort());
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator("#map-error").waitFor();
  await p.locator('[data-focus="0"]').click();
  ensure(
    (await p.locator(".leg").count()) > 0,
    "Leg focus remains safe when the map library cannot load",
  );
  await p.locator('[data-edit="0"]').click();
  await p.locator("#find-next").click();
  await p.locator("[data-next]").first().waitFor();
  ensure(
    (await p.locator("[data-next]").count()) > 0,
    "Missing map library preserves connection editing",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/catalog", async (route) => {
    const response = await route.fetch(),
      catalog = await response.json();
    catalog.airports.find((a) => a.iata === "LGW").timezone = "Asia/Tokyo";
    await route.fulfill({ response, json: catalog });
  });
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p
    .getByRole("button", { name: /YYZ → LHR → AMS → CDG/ })
    .first()
    .click();
  await p.locator('[data-edit="0"]').click();
  await p.locator("#connection-form [name=window]").selectOption("any");
  await p.locator("#connection-form [name=airport-change]").check();
  await p.locator("#find-next").click();
  await p.locator("[data-next]").first().waitFor();
  const preview = p
    .locator(".next-option")
    .filter({ hasText: "LGW → AMS" })
    .first();
  ensure(
    (await preview.innerText()).includes("GMT+1") &&
      !(await preview.innerText()).includes("GMT+9"),
    "Next-flight preview uses calculation snapshot timezone, not stale catalog",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/itineraries/search/local", async (route) => {
    const response = await route.fetch(),
      result = await response.json();
    result.airports = result.airports.filter((a) => a.iata !== "YYZ");
    await route.fulfill({ response, json: result });
  });
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  ensure(
    (await p.locator(".leg-time").first().textContent()).includes("GMT") &&
      !(await p.locator(".leg-time").first().textContent()).includes("GMT-4"),
    "Missing snapshot timezone falls back to UTC, without old catalog facts",
  );
  ensure(
    (await p.locator("#map-info").textContent()).includes(
      "without coordinates",
    ),
    "Map discloses missing snapshot coordinates",
  );
  await context.close();

  ({ context, p } = await fresh({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 1,
  }));
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator(".mobile-views [data-view=map]").click();
  await p.locator(".map-land").first().waitFor({ state: "attached" });
  ensure(
    await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    "Touch phone: no horizontal overflow",
  );
  ensure(
    await p
      .locator(".mobile-views button")
      .evaluateAll((nodes) =>
        nodes.every((n) => n.getBoundingClientRect().height >= 44),
      ),
    "Touch phone: view controls have 44px targets",
  );
  await p.screenshot({
    path: path.join(out, "touch-phone-map.png"),
    fullPage: true,
  });
  await context.close();

  ({ context, p } = await fresh());
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  async function tabTo(predicate, max = 100) {
    for (let n = 0; n < max; n++) {
      if (await p.evaluate(predicate)) return;
      await p.keyboard.press("Tab");
    }
    throw Error("Keyboard target unreachable");
  }
  await tabTo(() => document.activeElement?.dataset.option === "1");
  await p.keyboard.press("Enter");
  ensure(
    (await p.locator('[data-option="1"]').getAttribute("aria-pressed")) ===
      "true",
    "Keyboard selects a dated journey",
  );
  ensure(
    await p.evaluate(() => document.activeElement?.dataset.option === "1"),
    "Keyboard selection retains focus after rendering",
  );
  await tabTo(() => document.activeElement?.dataset.edit === "0");
  await p.keyboard.press("Enter");
  ensure(
    await p.locator("#connection-editor").isVisible(),
    "Keyboard opens connection editor",
  );
  await tabTo(() => document.activeElement?.id === "find-next");
  await p.keyboard.press("Enter");
  await p.locator("[data-next]").first().waitFor();
  await tabTo(() => document.activeElement?.dataset.next === "0");
  await p.keyboard.press("Enter");
  await p.waitForFunction(
    () => document.getElementById("connection-editor").hidden,
  );
  ensure(
    await p.evaluate(() => document.activeElement?.id === "undo"),
    "Keyboard applies next leg and lands on undo",
  );
  await p.keyboard.press("Enter");
  ensure(
    (await p.locator("#status").textContent()).includes(
      "Previous journey restored",
    ),
    "Keyboard undoes route edit",
  );
  ensure(
    await p
      .locator("button")
      .evaluateAll((nodes) =>
        nodes.every(
          (n) => n.textContent.trim() || n.getAttribute("aria-label"),
        ),
      ),
    "All buttons have accessible names",
  );
  await context.close();
  ({ context, p } = await fresh());
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator("[name=origin]").fill("yyz");
  await p.locator("[name=destination]").fill("cdg");
  await p.locator("[name=via]").fill("ams");
  const typed = p.waitForResponse((response) =>
    response.url().endsWith("/v1/itineraries/search/local"),
  );
  await p.locator("[name=via]").press("Enter");
  const typedResult = await (await typed).json();
  await p.waitForFunction(
    () => !document.getElementById("search-button").disabled,
  );
  ensure(
    typedResult.query.origin === "YYZ" &&
      typedResult.query.destination === "CDG" &&
      typedResult.query.via_airport === "AMS",
    "Lowercase airport entry and Enter submit canonical codes",
  );
  ensure(
    typedResult.items.length > 0 &&
      typedResult.items.every((item) =>
        item.legs.some((f) => f.destination_iata === "AMS"),
      ),
    "Typed via airport constrains real engine results",
  );
  ensure(
    new URL(p.url()).searchParams.get("via") === "AMS",
    "Search URL retains required via airport",
  );
  await p.locator('[data-edit="0"]').click();
  ensure(
    await p
      .locator("[name=next-airport]")
      .evaluate((el) => el === document.activeElement),
    "Connection editor focuses typed next-airport entry",
  );
  await p.locator("[name=next-airport]").fill("ams");
  const next = p.waitForResponse((response) =>
    response.url().endsWith("/v1/plans/rebuild"),
  );
  await p.locator("[name=next-airport]").press("Enter");
  const nextResult = await (await next).json();
  await p.locator("[data-next]").first().waitFor();
  ensure(
    nextResult.options.length > 0 &&
      nextResult.options.every(
        (option) => option.flight.destination_iata === "AMS",
      ),
    "Typed next airport is enforced before the option list",
  );
  ensure(
    (await next).request().postDataJSON().via_airport === "AMS",
    "Connection calculation retains the search via requirement",
  );
  await p.locator('[data-next="0"]').click();
  await p.waitForFunction(
    () => document.getElementById("connection-editor").hidden,
  );
  ensure(
    (await p.locator(".leg").nth(1).getAttribute("data-provider-id")).includes(
      "ams",
    ),
    "Applying typed next airport retains the selected prefix and completes its suffix",
  );
  const typedExport = p.waitForEvent("download");
  await p.locator("#export").click();
  const artifact = await typedExport;
  await artifact.saveAs(path.join(out, "typed-airport-export.json"));
  ensure(
    JSON.parse(
      fs.readFileSync(path.join(out, "typed-airport-export.json"), "utf8"),
    ).query.via_airport === "AMS",
    "Export retains the required airport constraint",
  );
  await p.screenshot({
    path: path.join(out, "typed-airports.png"),
    fullPage: true,
  });
  await p.locator('[data-edit="0"]').click();
  await p.locator("[name=next-airport]").fill("ABC");
  await p.locator("[name=next-airport]").press("Enter");
  await p.getByText("No next flight matches", { exact: false }).waitFor();
  ensure(
    (await p.locator("[data-next]").count()) === 0,
    "Unlisted valid next-airport code returns honest empty options",
  );
  await p.locator("#close-editor").click();
  await p.locator("[name=via]").fill("");
  await p.locator("[name=origin]").fill("abc");
  await p.locator("[name=origin]").press("Enter");
  await p.locator("#error").waitFor();
  ensure(
    (await p.locator("#error").textContent()).includes("timezone metadata"),
    "Unlisted typed airport gets actionable local-time metadata guidance",
  );
  await p.locator("[name=input-clock]").selectOption("UTC");
  await p.locator("#search-button").click();
  await p
    .getByText("No matching route in loaded data.", { exact: false })
    .waitFor();
  ensure(
    (await p.locator("[name=origin]").inputValue()) === "ABC",
    "Unlisted code remains editable for UTC searches",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/plans/rebuild", async (route) => {
    const response = await route.fetch({
      postData: JSON.stringify({
        ...route.request().postDataJSON(),
        limits: { max_candidates: 1 },
      }),
    });
    await route.fulfill({ response });
  });
  await p.goto(base);
  await p.locator("[data-option]").first().waitFor();
  await p.locator('[data-edit="0"]').click();
  await p.locator("[name=next-airport]").fill("CDG");
  await p.locator("[name=next-airport]").press("Enter");
  await p.waitForFunction(() => !document.getElementById("find-next").disabled);
  ensure(
    (await p.locator("#next-options").textContent()).includes("stopped early"),
    "Filtered next-airport search distinguishes partial results from no matches",
  );
  ensure(
    (await p.locator(".leg").count()) > 0,
    "Partial connection search retains the previous selected journey",
  );
  await context.close();

  ({ context, p } = await fresh());
  await p.route("**/v1/catalog", async (route) => {
    const response = await route.fetch(),
      catalog = await response.json();
    catalog.airports = [];
    await route.fulfill({ response, json: catalog });
  });
  await p.goto(base);
  await p.locator("#search-form").waitFor();
  await p.waitForFunction(() =>
    document.getElementById("dataset-tag").textContent.includes("12 records"),
  );
  await p.locator("[name=origin]").fill("YYZ");
  await p.locator("[name=destination]").fill("CDG");
  await p.locator("[name=input-clock]").selectOption("UTC");
  ensure(
    await p.locator("#search-button").isEnabled(),
    "Search remains available with no airport suggestions",
  );
  await p.locator("#search-button").click();
  await p.locator("[data-option]").first().waitFor();
  ensure(
    (await p.locator("[data-option]").count()) > 0,
    "Typed UTC airports search actual records without a picker catalog",
  );
  await context.close();

  ensure(
    errors.length === 0,
    "No JavaScript runtime errors across happy and recovery flows",
  );
  ensure(
    !network.some((url) => !url.startsWith(base)),
    "All audited pages make only same-origin runtime requests",
  );
  const files = [
    "src/server/web/index.html",
    "src/server/web/app.css",
    "src/server/web/app.mjs",
    "src/server/web/map.mjs",
    "src/server/web/format.mjs",
    "scripts/qa_web.cjs",
  ];
  fs.writeFileSync(
    path.join(out, "results.json"),
    JSON.stringify(
      {
        url: base,
        browser_version: browser.version(),
        node_version: process.version,
        recorded_at_utc: new Date().toISOString(),
        checks,
        errors,
        expected_fault_console: expectedHTTPConsole,
        source_sha256: Object.fromEntries(files.map((p) => [p, sha(p)])),
      },
      null,
      2,
    ) + "\n",
  );
  await browser.close();
  console.log(`Finished ${checks.length} checks`);
})().catch((error) => {
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(
    path.join(out, "failure.json"),
    JSON.stringify(
      { message: error.message, stack: error.stack, checks, errors },
      null,
      2,
    ),
  );
  console.error(error);
  process.exit(1);
});
