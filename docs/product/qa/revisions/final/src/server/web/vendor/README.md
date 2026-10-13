# Map assets

These files are served locally. No CDN or build step is required at runtime.

- `d3-7.9.0.min.js`: D3 7.9.0, copied verbatim from the preserved R06 preview, with the adjacent ISC license.
- `world-110m.geojson`: the 177-feature world geometry already verified in R06, extracted from its `fr-world` data block. Original source: `@d3-maps/atlas` 1.0.0, `world/countries/countries-110m`, converted from TopoJSON to GeoJSON during the design exploration. The adjacent atlas license is retained.

Airport coordinates and IANA time zones come from the read-only query response. The static geometry supplies the basemap, not flight or airport facts.
