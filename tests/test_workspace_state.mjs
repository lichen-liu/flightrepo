import assert from "node:assert/strict";
import test from "node:test";
import { hasSearchDraft, rankedRequest, reconcileSortedSelection, readMapPreference, writeMapPreference } from "../src/server/web/workspace-state.mjs";

test("sorting reuses the applied query and revision without mutating it or consuming a draft", () => {
  const applied = { url: "/search/local", body: { origin: "AAA", destination: "BBB", ranking: "fewest_legs", offset: 40, maximum_connection_minutes: 10080 } };
  const request = rankedRequest(applied, "shortest_elapsed", "rev-2");
  assert.deepEqual(request.body, { ...applied.body, ranking: "shortest_elapsed", offset: 0, data_revision: "rev-2" });
  assert.equal(applied.body.offset, 40);
  assert.equal(applied.body.ranking, "fewest_legs");
  assert.equal(hasSearchDraft({ origin: "CCC", ranking: "shortest_elapsed" }, { origin: "AAA", ranking: "fewest_legs" }), true);
  assert.equal(hasSearchDraft({ origin: "AAA", ranking: "shortest_elapsed" }, { origin: "AAA", ranking: "fewest_legs" }), false);
});

test("sorting preserves selected identity and camera but uses returned facts", () => {
  const selected = { itinerary_id: "second", legs: ["old"] };
  const result = { data_revision: "same", items: [{ itinerary_id: "first" }, { itinerary_id: "second", legs: ["fresh"] }], airports: [], diagnostics: {} };
  const next = reconcileSortedSelection(selected, "same", result);
  assert.equal(next.selection.itinerary_id, "second");
  assert.deepEqual(next.selection.legs, ["fresh"]);
  assert.equal(next.preserveView, true);
  const changed = reconcileSortedSelection(selected, "old-revision", result);
  assert.equal(changed.preserveView, false);
  assert.deepEqual(changed.selection.legs, ["fresh"]);
});

test("sorting retains an edited journey only for the same data revision", () => {
  const selected = { itinerary_id: "edited", edited: true, legs: ["custom"] };
  const result = { data_revision: "same", items: [{ itinerary_id: "first", legs: ["loaded"] }], airports: [], diagnostics: {} };
  const next = reconcileSortedSelection(selected, "same", result);
  assert.equal(next.selection, selected);
  assert.equal(next.preserveHistory, true);
  assert.equal(reconcileSortedSelection(selected, "different", result).selection.itinerary_id, "first");
  assert.equal(reconcileSortedSelection(selected, "different", { ...result, items: [] }).selection, null);
});

test("sorting keeps a selected journey that ranks beyond the returned page", () => {
  const selected = { itinerary_id: "later-page", edited: false, legs: ["saved"] };
  const result = { data_revision: "same", items: [{ itinerary_id: "first" }], airports: [], diagnostics: {} };
  assert.deepEqual(reconcileSortedSelection(selected, "same", result), {
    selection: selected, preserveView: true, preserveHistory: true,
  });
  assert.equal(reconcileSortedSelection(selected, "old", result).selection.itinerary_id, "first");
  assert.equal(reconcileSortedSelection(selected, "same", { ...result, items: [] }).selection, selected);
});

test("map preference survives reload while invalid and blocked storage are harmless", () => {
  const entries = new Map();
  const storage = { getItem: key => entries.get(key), setItem: (key, value) => entries.set(key, value) };
  assert.equal(readMapPreference(storage), "route");
  writeMapPreference(storage, "globe");
  assert.equal(readMapPreference(storage), "globe");
  writeMapPreference(storage, "invalid");
  assert.equal(readMapPreference(storage), "globe");
  entries.set("flyji.map-view.v1", "invalid");
  assert.equal(readMapPreference(storage), "route");
  const blocked = { getItem() { throw Error("disabled"); }, setItem() { throw Error("disabled"); } };
  assert.equal(readMapPreference(blocked), "route");
  assert.doesNotThrow(() => writeMapPreference(blocked, "globe"));
});
