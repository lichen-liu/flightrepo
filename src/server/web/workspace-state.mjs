const MAP_MODES = new Set(["route", "globe", "flat"]);
const MAP_KEY = "flyji.map-view.v1";

export function readMapPreference(storage) {
  try {
    const mode = storage?.getItem(MAP_KEY);
    return MAP_MODES.has(mode) ? mode : "route";
  } catch { return "route"; }
}
export function writeMapPreference(storage, mode) {
  if (!MAP_MODES.has(mode)) return;
  try { storage?.setItem(MAP_KEY, mode); } catch { /* Private storage can be unavailable. */ }
}
export function hasSearchDraft(current, applied) {
  if (!applied) return false;
  return Object.keys(current).some((key) => key !== "ranking" && current[key] !== applied[key]);
}
export function rankedRequest(applied, ranking, revision) {
  return {
    url: applied.url,
    body: { ...applied.body, ranking, offset: 0, ...(revision ? { data_revision: revision } : {}) },
  };
}
export function reconcileSortedSelection(selected, oldRevision, result) {
  const unchanged = oldRevision != null && oldRevision === result.data_revision;
  if (selected?.edited && unchanged)
    return { selection: selected, preserveView: true, preserveHistory: true };
  const matched = result.items.find((item) => item.itinerary_id === selected?.itinerary_id);
  // Reordering starts a new page; the selected route may rank beyond that page.
  if (selected && unchanged && !matched)
    return { selection: selected, preserveView: true, preserveHistory: true };
  const item = matched || result.items[0];
  return {
    selection: item ? { ...item, airports: result.airports, diagnostics: result.diagnostics, complete: true, edited: false } : null,
    preserveView: Boolean(matched && unchanged),
    preserveHistory: Boolean(matched && unchanged),
  };
}
