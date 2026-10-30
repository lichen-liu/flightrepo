import { stamp, departureTime, arrivalTime, flightLabel } from "./format.mjs";

export function routeGeometry(legs, point) {
  const codes = [...new Set(legs.flatMap((flight) => [flight.origin_iata, flight.destination_iata]))],
    points = codes.map(point).filter(Boolean),
    lines = [];
  let missing = 0;
  for (let i = 0; i < legs.length; i++) {
    const flight = legs[i], a = point(flight.origin_iata), b = point(flight.destination_iata);
    if (a && b) lines.push({ type: "flight", coordinates: [a, b] });
    else missing++;
    if (i && legs[i - 1].destination_iata !== flight.origin_iata) {
      const arrival = point(legs[i - 1].destination_iata);
      if (arrival && a) lines.push({ type: "transfer", coordinates: [arrival, a] });
    }
  }
  return { codes, points, lines, missing };
}

export function routeProjection(d3, geometry, width, height) {
  const { points, lines } = geometry,
    marginX = Math.min(36, width * 0.12),
    marginY = Math.min(28, height * 0.2),
    extent = [[marginX, marginY], [width - marginX, height - marginY]];
  if (!points.length)
    return d3.geoNaturalEarth1().fitExtent(extent, { type: "Sphere" });

  let widest = 0, pair = [points[0], points[0]];
  for (let i = 0; i < points.length; i++)
    for (let j = i + 1; j < points.length; j++) {
      const distance = d3.geoDistance(points[i], points[j]);
      if (distance > widest) { widest = distance; pair = [points[i], points[j]]; }
    }
  const candidates = [
    d3.geoCentroid({ type: "MultiPoint", coordinates: points }),
    d3.geoInterpolate(...pair)(0.5),
    points[0],
  ].filter((candidate) => candidate.every(Number.isFinite));
  const radius = (center) => Math.max(...points.map((point) => d3.geoDistance(center, point)));
  const center = candidates.reduce((best, next) => radius(next) < radius(best) ? next : best);
  const projection = d3.geoAzimuthalEquidistant()
    .rotate([-center[0], -center[1], 0])
    .clipAngle(179.9);
  // Fit sampled great-circle arcs, not longitude extrema: trans-Pacific routes
  // stay centered on the Pacific and bowed flight paths keep their padding.
  const samples = [...points];
  for (const line of lines) {
    const interpolate = d3.geoInterpolate(...line.coordinates),
      steps = Math.max(2, Math.ceil(d3.geoDistance(...line.coordinates) / (Math.PI / 36)));
    for (let step = 1; step < steps; step++) samples.push(interpolate(step / steps));
  }
  if (widest < 1e-8) {
    projection.scale(Math.min(width, height) * 2).translate([width / 2, height / 2]);
  } else {
    projection.fitExtent(extent, { type: "MultiPoint", coordinates: samples });
    // Nearby or coincident airports should retain geographic context rather
    // than producing an unbounded zoom level.
    projection.scale(Math.min(projection.scale(), Math.max(width, height) * 8));
    const bounds = d3.geoPath(projection).bounds({ type: "MultiPoint", coordinates: samples }),
      offset = projection.translate();
    projection.translate([
      offset[0] + width / 2 - (bounds[0][0] + bounds[1][0]) / 2,
      offset[1] + height / 2 - (bounds[0][1] + bounds[1][1]) / 2,
    ]);
  }
  return projection.clipExtent([[0, 0], [width, height]]);
}

export class RouteMap {
  constructor(svg, info) {
    this.svg = svg;
    this.info = info;
    this.mode = "route";
    this.rotation = [35, -35, 0];
    this.zoom = 1;
    this.airports = new Map();
    this.legs = [];
    this.cursor = null;
    this.world = null;
    this.d3 = window.d3;
    this.revision = 0;
    // SVG updates can change layout. Draw after resize delivery has finished.
    this.resize = new ResizeObserver(() => this.requestDraw());
    this.resize.observe(svg);
    let drag = null;
    svg.addEventListener("pointerdown", (e) => {
      if (this.mode === "globe") {
        drag = [e.clientX, e.clientY, ...this.rotation];
        if (e.pointerType !== "touch") svg.setPointerCapture(e.pointerId);
      }
    });
    svg.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag[0],
          dy = e.clientY - drag[1];
        if (e.pointerType === "touch" && Math.abs(dy) > Math.abs(dx)) return;
        this.rotation = [
          drag[2] + dx * 0.35,
          Math.max(-80, Math.min(80, drag[3] - dy * 0.35)),
          0,
        ];
        this.requestDraw();
      }
    });
    const stop = () => {
      drag = null;
    };
    svg.addEventListener("pointerup", stop);
    svg.addEventListener("pointercancel", stop);
    svg.addEventListener(
      "wheel",
      (event) => this.onWheel(event),
      { passive: false },
    );
  }
  async load() {
    if (!this.d3) throw Error("Map library unavailable.");
    const response = await fetch(new URL("./vendor/world-110m.geojson", import.meta.url));
    if (!response.ok) throw Error("World geometry unavailable.");
    this.world = await response.json();
    this.requestDraw();
  }
  setAirports(airports) {
    this.airports = new Map(airports.map((a) => [a.iata, a]));
    this.revision++;
    this.requestDraw();
  }
  point(code) {
    const a = this.airports.get(code);
    return a && Number.isFinite(a.longitude) && Number.isFinite(a.latitude)
      ? [a.longitude, a.latitude]
      : null;
  }
  setJourney(legs) {
    this.legs = legs || [];
    this.revision++;
    this.cursor = null;
    this.fit();
  }
  setMode(mode) {
    this.mode = mode;
    this.requestDraw();
  }
  rotate(degrees) {
    this.rotation[0] += degrees;
    this.requestDraw();
  }
  tilt(degrees) {
    this.rotation[1] = Math.max(-80, Math.min(80, this.rotation[1] + degrees));
    this.requestDraw();
  }
  adjustZoom(factor) {
    this.zoom = Math.max(0.8, Math.min(8, this.zoom * factor));
    this.requestDraw();
  }
  onWheel(event) {
    if (this.mode !== "globe" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    this.adjustZoom(Math.exp(-event.deltaY * 0.001));
  }
  fit() {
    const points = this.legs
      .flatMap((f) => [
        this.point(f.origin_iata),
        this.point(f.destination_iata),
      ])
      .filter(Boolean);
    if (points.length && this.d3) {
      const center = this.d3.geoCentroid({ type: "MultiPoint", coordinates: points });
      // A centroid overweights clustered stops and can hide a distant origin.
      // Also consider the midpoint of the widest pair, then minimize the
      // furthest angular distance from the camera to any journey airport.
      let widest = 0, pair = [points[0], points[0]];
      for (let i = 0; i < points.length; i++)
        for (let j = i + 1; j < points.length; j++) {
          const distance = this.d3.geoDistance(points[i], points[j]);
          if (distance > widest) { widest = distance; pair = [points[i], points[j]]; }
        }
      const candidates = [center, this.d3.geoInterpolate(...pair)(0.5)]
        .filter((candidate) => candidate.every(Number.isFinite));
      const radius = (c) => Math.max(...points.map((point) => this.d3.geoDistance(c, point)));
      const c = candidates.reduce((best, candidate) => radius(candidate) < radius(best) ? candidate : best);
      this.rotation = [-c[0], -c[1], 0];
    } else this.rotation = [35, -35, 0];
    this.zoom = 1;
    this.requestDraw();
  }
  focus(index) {
    const f = this.legs[index];
    if (!f || !this.d3) return;
    const a = this.point(f.origin_iata),
      b = this.point(f.destination_iata);
    if (a && b) {
      const c = this.d3.geoInterpolate(a, b)(0.5);
      this.rotation = [-c[0], -c[1], 0];
      this.mode = "globe";
      this.zoom = Math.max(1, Math.min(8, 1.4 / this.d3.geoDistance(a, b)));
      this.requestDraw();
    }
  }
  setCursor(instant) {
    this.cursor = instant;
    this.requestDraw();
  }
  position() {
    if (this.cursor == null || !this.legs.length || !this.d3) return null;
    for (let i = 0; i < this.legs.length; i++) {
      const f = this.legs[i],
        previous = this.legs[i - 1];
      const start = Date.parse(departureTime(f)),
        end = Date.parse(arrivalTime(f));
      if (this.cursor >= start && this.cursor <= end) {
        const a = this.point(f.origin_iata),
          b = this.point(f.destination_iata);
        return a && b
          ? this.d3.geoInterpolate(a, b)(end > start ? (this.cursor - start) / (end - start) : 1)
          : null;
      }
      // A timetable does not locate a traveler during an airport change.
      if (this.cursor < start)
        return previous && previous.destination_iata !== f.origin_iata
          ? null
          : this.point(f.origin_iata);
    }
    return this.point(this.legs.at(-1).destination_iata);
  }
  requestDraw() {
    if (this.drawFrame != null) return;
    this.drawFrame = requestAnimationFrame(() => {
      this.drawFrame = null;
      this.draw();
    });
  }
  draw() {
    if (!this.d3 || !this.world) return;
    const d3 = this.d3,
      w = this.svg.clientWidth,
      h = this.svg.clientHeight;
    if (!w || !h) return;
    const key = JSON.stringify([
      w,
      h,
      this.mode,
      this.rotation,
      this.zoom,
      this.revision,
    ]);
    if (key === this.staticKey) {
      this.drawPosition(this.projection);
      return;
    }
    const geometry = routeGeometry(this.legs, (code) => this.point(code));
    const p =
      this.mode === "globe"
        ? d3
            .geoOrthographic()
            .rotate(this.rotation)
            .translate([w / 2, h / 2])
            .scale(Math.min(w * 0.44, h * 0.44) * this.zoom)
            .clipAngle(90)
        : this.mode === "route"
          ? routeProjection(d3, geometry, w, h)
          : d3.geoNaturalEarth1().fitExtent(
            [
              [14, 12],
              [w - 14, h - 25],
            ],
            { type: "Sphere" },
          );
    const path = d3.geoPath(p),
      s = d3.select(this.svg).attr("viewBox", `0 0 ${w} ${h}`).attr("data-mode", this.mode);
    s.selectAll("*").remove();
    this.staticKey = key;
    this.projection = p;
    s.append("title").text(
      this.legs.length
        ? `Flight route ${this.legs.map((f) => f.origin_iata).join(" → ")} → ${this.legs.at(-1).destination_iata}`
        : "World map",
    );
    const defs = s.append("defs");
    const ocean = defs.append("radialGradient").attr("id", "atlas-ocean").attr("cx", "35%").attr("cy", "25%").attr("r", "80%");
    ocean.append("stop").attr("offset", "0%").attr("stop-color", "#f3f4ff");
    ocean.append("stop").attr("offset", "70%").attr("stop-color", "#ccd3f5");
    ocean.append("stop").attr("offset", "100%").attr("stop-color", "#929edc");
    if (this.mode === "globe") {
      s.append("circle").attr("class", "map-atmosphere").attr("cx", w / 2).attr("cy", h / 2).attr("r", p.scale());
      s.append("circle").attr("class", "map-orbit").attr("cx", w / 2).attr("cy", h / 2).attr("r", p.scale() * 1.09);
    }
    s.append("path")
      .datum({ type: "Sphere" })
      .attr("class", "map-ocean")
      .attr("d", path);
    s.append("path")
      .datum(d3.geoGraticule10())
      .attr("class", "map-grid")
      .attr("d", path);
    s.append("path")
      .datum(this.world)
      .attr("class", "map-land")
      .attr("d", path);
    const { lines, missing, codes } = geometry;
    s.selectAll("path.map-route")
      .data(lines)
      .join("path")
      .attr("class", (x) => `map-route map-${x.type}`)
      .attr("d", (x) =>
        path({ type: "LineString", coordinates: x.coordinates }),
      );
    const visible = (point) =>
      this.mode !== "globe" ||
      d3.geoDistance(point, d3.geoRotation(p.rotate()).invert([0, 0])) <
        Math.PI / 2;
    const placed = [];
    const labelOrder = this.legs.length
      ? [
          ...new Set([
            this.legs[0].origin_iata,
            this.legs.at(-1).destination_iata,
            ...codes,
          ]),
        ]
      : codes;
    for (const code of labelOrder) {
      const a = this.point(code);
      if (!a || !visible(a)) continue;
      const xy = p(a);
      if (!xy || xy[0] < 7 || xy[0] > w - 7 || xy[1] < 7 || xy[1] > h - 7)
        continue;
      s.append("circle")
        .attr("class", "map-airport")
        .attr("cx", xy[0])
        .attr("cy", xy[1])
        .attr("r", 4);
      // Declutter nearby airports; exact names remain in the journey details.
      if (
        placed.some(
          (v) => Math.abs(v[0] - xy[0]) < 42 && Math.abs(v[1] - xy[1]) < 20,
        )
      )
        continue;
      placed.push(xy);
      s.append("text")
        .attr("class", "map-airport-label")
        .attr("x", xy[0] + (xy[0] > w - 50 ? -8 : 8))
        .attr("y", xy[1] - 8)
        .attr("text-anchor", xy[0] > w - 50 ? "end" : "start")
        .text(code);
    }
    this.drawPosition(p);
    this.svg.setAttribute(
      "aria-label",
      this.legs.length
        ? `Flight route. ${codes.join(", ")}. ${missing ? `${missing} legs lack coordinates.` : ""}`
        : "World map. Choose a journey to display its route.",
    );
    this.info.textContent = missing
      ? `${missing} leg${missing === 1 ? "" : "s"} without coordinates`
      : this.mode === "globe"
        ? "Drag to rotate · Ctrl/⌘ + scroll to zoom"
        : "Great-circle flight paths · dashed airport changes";
  }
  drawPosition(projection) {
    const position = this.position(),
      d3 = this.d3;
    const visible =
      position &&
      (this.mode !== "globe" ||
        d3.geoDistance(
          position,
          d3.geoRotation(projection.rotate()).invert([0, 0]),
        ) <
          Math.PI / 2);
    d3.select(this.svg)
      .selectAll(".map-position")
      .data(visible ? [projection(position)] : [])
      .join("circle")
      .attr("class", "map-position")
      .attr("cx", (p) => p[0])
      .attr("cy", (p) => p[1])
      .attr("r", 6);
  }
}

const timelineScenes = new WeakMap();

export function drawTimeline(container, legs, cursor) {
  const clear = () => {
    container.replaceChildren();
    timelineScenes.delete(container);
  };
  if (!legs?.length) { clear(); return; }
  const d3 = window.d3,
    w = container.clientWidth - 36;
  if (!d3 || w < 100) { clear(); return; }
  const times = legs.map((flight) => [departureTime(flight), arrivalTime(flight)]),
    key = JSON.stringify([w, times]);
  let scene = timelineScenes.get(container);
  // Scrubbing changes only the cursor. Keep the flight bars and tick axis until
  // their geometry changes, including edits that retain the same legs array.
  if (!scene || scene.key !== key || scene.node !== container.firstElementChild) {
    clear();
    scene = buildTimeline(d3, container, w, times);
    timelineScenes.set(container, { ...scene, key });
  }
  const at = cursor == null ? NaN : scene.x(new Date(cursor));
  scene.svg.selectAll(".timeline-cursor")
    .data(Number.isFinite(at) ? [at] : [])
    .join("line")
    .attr("class", "timeline-cursor")
    .attr("x1", (position) => position)
    .attr("x2", (position) => position)
    .attr("y1", 5)
    .attr("y2", 34)
    .attr("stroke", "var(--cursor)")
    .attr("stroke-width", 2);
}

function buildTimeline(d3, container, w, times) {
  const start = new Date(times[0][0]),
    end = new Date(times.at(-1)[1]);
  const x = d3
    .scaleUtc()
    .domain([start, end])
    .range([7, w - 7]);
  const svg = d3
    .select(container)
    .append("svg")
    .attr("viewBox", `0 0 ${w} 79`)
    .attr("width", "100%")
    .attr("role", "img")
    .attr(
      "aria-label",
      `UTC itinerary times from ${stamp(start)} to ${stamp(end)}, including flight and connection durations.`,
    );
  for (let i = 0; i < times.length; i++) {
    const a = x(new Date(times[i][0])),
      b = x(new Date(times[i][1]));
    if (i) {
      const prev = x(new Date(times[i - 1][1]));
      svg
        .append("line")
        .attr("x1", prev)
        .attr("x2", a)
        .attr("y1", 20)
        .attr("y2", 20)
        .attr("stroke", "var(--ground)")
        .attr("stroke-width", 3)
        .attr("stroke-dasharray", "3 3");
    }
    svg
      .append("rect")
      .attr("x", a)
      .attr("y", 11)
      .attr("width", Math.max(1, b - a))
      .attr("height", 18)
      .attr("rx", 3)
      .attr("fill", "var(--flight)");
  }
  const ticks = (w < 420 ? x.ticks(3) : x.ticks(5)).filter(
    (d) => x(d) > 26 && x(d) < w - 26,
  );
  svg
    .append("g")
    .attr("transform", "translate(0,42)")
    .call(
      d3
        .axisBottom(x)
        .tickValues(ticks)
        .tickFormat(d3.utcFormat("%d %b"))
        .tickSize(4),
    );
  svg
    .selectAll(".tick")
    .append("text")
    .attr("y", 28)
    .attr("fill", "var(--muted)")
    .text((d) => d3.utcFormat("%H:%M")(d));
  svg
    .append("text")
    .attr("x", w - 7)
    .attr("y", 8)
    .attr("text-anchor", "end")
    .text("UTC");
  return { svg, x, node: svg.node() };
}
