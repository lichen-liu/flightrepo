import { escapeHTML as e } from "./format.mjs";

export function qualityLabels(flights, { details = false } = {}) {
  const seen = new Set(), labels = [];
  for (const f of flights) for (const label of f.quality?.labels || []) {
    if (!["info", "caution", "warning"].includes(label.severity) || seen.has(label.code)) continue;
    if (!details && label.severity === "info" && (label.code !== "vrs_route_match" || !flights.every((leg) => leg.route_verified))) continue;
    seen.add(label.code); labels.push(label);
  }
  return labels.sort((a, b) => ({ warning: 0, caution: 1, info: 2 }[a.severity] - { warning: 0, caution: 1, info: 2 }[b.severity]));
}

export function qualityBadges(flights, options) {
  return qualityLabels(flights, options).map((x) => `<span class="quality-badge quality-${x.severity}" title="${e(x.detail)}">${e(x.label)}</span>`).join("");
}

export function qualityDetails(flight) {
  return qualityLabels([flight], { details: true }).map((x) => `<div class="quality-detail quality-${x.severity}"><strong>${e(x.label)}</strong> · ${e(x.detail)}</div>`).join("");
}
