import { stamp, departureTime, arrivalTime, flightLabel } from "./format.mjs";

export class RouteMap {
  constructor(svg, info) {
    this.svg = svg;
    this.info = info;
    this.mode = "globe";
    this.rotation = [35, -35, 0];
    this.zoom = 1;
    this.airports = new Map();
    this.legs = [];
    this.cursor = null;
    this.world = null;
    this.d3 = window.d3;
    this.revision = 0;
    this.resize = new ResizeObserver(() => this.draw());
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
        this.draw();
      }
    });
    const stop = () => {
      drag = null;
    };
    svg.addEventListener("pointerup", stop);
    svg.addEventListener("pointercancel", stop);
    svg.addEventListener(
      "wheel",
      (e) => {
        if (this.mode === "globe") {
          e.preventDefault();
          this.adjustZoom(Math.exp(-e.deltaY * 0.001));
        }
      },
      { passive: false },
    );
  }
  async load() {
    if (!this.d3) throw Error("Map library unavailable.");
    const response = await fetch("/assets/vendor/world-110m.geojson");
    if (!response.ok) throw Error("World geometry unavailable.");
    this.world = await response.json();
    this.draw();
  }
  setAirports(airports) {
    this.airports = new Map(airports.map((a) => [a.iata, a]));
    this.revision++;
    this.draw();
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
    this.draw();
  }
  rotate(degrees) {
    this.rotation[0] += degrees;
    this.draw();
  }
  tilt(degrees) {
    this.rotation[1] = Math.max(-80, Math.min(80, this.rotation[1] + degrees));
    this.draw();
  }
  adjustZoom(factor) {
    this.zoom = Math.max(0.8, Math.min(8, this.zoom * factor));
    this.draw();
  }
  fit() {
    const points = this.legs
      .flatMap((f) => [
        this.point(f.origin_iata),
        this.point(f.destination_iata),
      ])
      .filter(Boolean);
    if (points.length && this.d3) {
      const c = this.d3.geoCentroid({
        type: "MultiPoint",
        coordinates: points,
      });
      this.rotation = [-c[0], -c[1], 0];
    } else this.rotation = [35, -35, 0];
    this.zoom = 1;
    this.draw();
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
      this.draw();
    }
  }
  setCursor(instant) {
    this.cursor = instant;
    this.draw();
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
          ? this.d3.geoInterpolate(a, b)((this.cursor - start) / (end - start))
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
    const p =
      this.mode === "globe"
        ? d3
            .geoOrthographic()
            .rotate(this.rotation)
            .translate([w / 2, h / 2])
            .scale(Math.min(w * 0.44, h * 0.44) * this.zoom)
            .clipAngle(90)
        : d3.geoNaturalEarth1().fitExtent(
            [
              [14, 12],
              [w - 14, h - 25],
            ],
            { type: "Sphere" },
          );
    const path = d3.geoPath(p),
      s = d3.select(this.svg).attr("viewBox", `0 0 ${w} ${h}`);
    s.selectAll("*").remove();
    this.staticKey = key;
    this.projection = p;
    s.append("title").text(
      this.legs.length
        ? `Scheduled route ${this.legs.map((f) => f.origin_iata).join(" → ")} → ${this.legs.at(-1).destination_iata}`
        : "World map",
    );
    s.append("path")
      .datum({ type: "Sphere" })
      .attr("class", "map-ocean")
      .attr("d", path);
    s.append("path")
      .datum(d3.geoGraticule10())
      .attr("class", "map-grid")
      .attr("d", path);
    s.selectAll("path.map-land")
      .data(this.world.features)
      .join("path")
      .attr("class", "map-land")
      .attr("d", path);
    const lines = [];
    let missing = 0;
    for (let i = 0; i < this.legs.length; i++) {
      const f = this.legs[i],
        a = this.point(f.origin_iata),
        b = this.point(f.destination_iata);
      if (a && b) lines.push({ type: "flight", coordinates: [a, b] });
      else missing++;
      if (i && this.legs[i - 1].destination_iata !== f.origin_iata) {
        const g = this.point(this.legs[i - 1].destination_iata);
        if (g && a) lines.push({ type: "transfer", coordinates: [g, a] });
      }
    }
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
    const codes = [
      ...new Set(this.legs.flatMap((f) => [f.origin_iata, f.destination_iata])),
    ];
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
        ? `Scheduled flight route. ${codes.join(", ")}. ${missing ? `${missing} legs lack coordinates.` : ""}`
        : "World map. Choose a journey to display its route.",
    );
    this.info.textContent = missing
      ? `${missing} leg${missing === 1 ? "" : "s"} without coordinates`
      : this.mode === "globe"
        ? "Rotate or zoom with the controls; drag and scroll also work."
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

export function drawTimeline(container, legs, cursor) {
  container.replaceChildren();
  if (!legs?.length) return;
  const d3 = window.d3,
    w = container.clientWidth - 36;
  if (!d3 || w < 100) return;
  const start = new Date(departureTime(legs[0])),
    end = new Date(arrivalTime(legs.at(-1)));
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
      `UTC schedule from ${stamp(start)} to ${stamp(end)}, including flight and connection durations.`,
    );
  for (let i = 0; i < legs.length; i++) {
    const f = legs[i],
      a = x(new Date(departureTime(f))),
      b = x(new Date(arrivalTime(f)));
    if (i) {
      const prev = x(new Date(arrivalTime(legs[i - 1])));
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
  if (cursor != null) {
    const at = x(new Date(cursor));
    svg
      .append("line")
      .attr("x1", at)
      .attr("x2", at)
      .attr("y1", 5)
      .attr("y2", 34)
      .attr("stroke", "var(--cursor)")
      .attr("stroke-width", 2);
  }
}
