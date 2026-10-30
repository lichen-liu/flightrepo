import assert from "node:assert/strict";
import test from "node:test";
import d3 from "../src/server/web/vendor/d3-7.9.0.min.js";
import { RouteMap, routeGeometry, routeProjection } from "../src/server/web/map.mjs";

const dimensions = [500, 170];
const inside = ([x, y]) => Number.isFinite(x) && Number.isFinite(y) && x >= 12 && x <= 488 && y >= 12 && y <= 158;

test("route geography fits every airport and the curved flight arcs in a short viewport", () => {
  const points = [[-79.6, 43.7], [-0.5, 51.5], [4.8, 52.3], [2.5, 49]],
    lines = points.slice(1).map((point, index) => ({ coordinates: [points[index], point] })),
    projection = routeProjection(d3, { points, lines }, ...dimensions);
  assert.ok(points.map(projection).every(inside));
  for (const line of lines) {
    const interpolate = d3.geoInterpolate(...line.coordinates);
    for (let step = 0; step <= 100; step++) assert.ok(inside(projection(interpolate(step / 100))));
  }
});

test("a dateline crossing uses a Pacific-centered route rather than a whole-world extent", () => {
  const points = [[170, 35], [-170, 35]],
    projection = routeProjection(d3, { points, lines: [{ coordinates: points }] }, ...dimensions),
    center = d3.geoRotation(projection.rotate()).invert([0, 0]);
  assert.ok(Math.abs(center[0]) > 150);
  assert.ok(points.map(projection).every(inside));
  assert.ok(Math.abs(projection(points[0])[0] - projection(points[1])[0]) > 350);
});

test("missing coordinates retain known airports and permitted transfer geometry", () => {
  const airports = { AAA: [10, 40], BBB: [20, 45], CCC: [21, 45] },
    geometry = routeGeometry([
      { origin_iata: "AAA", destination_iata: "BBB" },
      { origin_iata: "CCC", destination_iata: "DDD" },
    ], (code) => airports[code] || null);
  assert.equal(geometry.missing, 1);
  assert.deepEqual(geometry.points, Object.values(airports));
  assert.deepEqual(geometry.lines.map((line) => line.type), ["flight", "transfer"]);
  const projection = routeProjection(d3, geometry, ...dimensions);
  assert.ok(geometry.points.map(projection).every(inside));
});

test("coincident airports and empty routes use finite sensible projections", () => {
  for (const points of [[[2, 49]], [[2, 49], [2, 49]], []]) {
    const projection = routeProjection(d3, { points, lines: [] }, ...dimensions);
    assert.ok(Number.isFinite(projection.scale()));
    assert.ok(projection.scale() > 0);
    assert.ok(points.map(projection).every(inside));
    const sphere = d3.geoPath(projection)({ type: "Sphere" });
    assert.ok(sphere && !sphere.includes("NaN") && !sphere.includes("Infinity"));
  }
});

test("Earth fitting stays finite for antipodal airports with an undefined spherical centroid", () => {
  const map = Object.create(RouteMap.prototype),
    points = {AAA:[0, 0], BBB:[180, 0]};
  map.d3 = d3;
  map.legs = [{origin_iata:"AAA",destination_iata:"BBB"}];
  map.point = code => points[code];
  map.requestDraw = () => {};
  assert.ok(d3.geoCentroid({type:"MultiPoint",coordinates:Object.values(points)}).some(value => !Number.isFinite(value)));
  map.fit();
  assert.ok(map.rotation.every(Number.isFinite));
  const projection = d3.geoOrthographic().rotate(map.rotation).scale(100),
    sphere = d3.geoPath(projection)({type:"Sphere"});
  assert.ok(sphere && !sphere.includes("NaN"));
});

test("a zero-duration flight places its time cursor at arrival without NaN coordinates", () => {
  const map = Object.create(RouteMap.prototype),
    points = {AAA:[0, 0], BBB:[5, 10]};
  map.d3 = d3;
  map.point = code => points[code];
  map.legs = [{origin_iata:"AAA",destination_iata:"BBB",departure_scheduled:"2026-10-17T12:00:00Z",arrival_scheduled:"2026-10-17T12:00:00Z"}];
  map.cursor = Date.parse("2026-10-17T12:00:00Z");
  const position = map.position();
  assert.ok(position.every(Number.isFinite));
  assert.ok(d3.geoDistance(position, points.BBB) < 1e-10);
});
