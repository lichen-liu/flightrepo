# Historical ingestion pilots and storage estimates

These measurements predate the final admission policy. They are retained for
research provenance, not operational instructions or current coverage promises.
The current workflow is [the ingestion pipeline](../ingestion.md).

## January 2024 pilot (historical design)

The pilot ran relaxed rules in a separate database before replacing the July–December
2023 extraction. Prepare December 31 through February 1 for overnight context;
report January departures separately from these boundary days. Use only ADSB.lol
observations and the airport reference already used by extraction. There are no
tail lookup APIs, commercial operator lists or external flight-validation joins.

Report daily candidate/searchable/review counts, available aircraft types,
exclusions and rejection reasons, database bytes, and successful raw-input cleanup.
`metadata.accepted_under_relaxed_review` counts evaluated candidates that no longer
need review because of the relaxed gap/callsign rules. These candidate events can
include later boundary refinements; use database counts for unique itineraries.
Treat a pilot as operationally successful only after all days commit, database
integrity/foreign-key and duplicate checks pass, itinerary time bounds are valid,
and search/calendar can read eligible records. Inspect representative airport
pairs and unusually long legs using retained itinerary fields. This is a
best-effort reconstruction, not a completeness or accuracy guarantee. Airport
estimates alone were searchable under that historical policy. The final policy
admits all four known uncertainty categories with labels.

## Historical measured storage

The prepared July–December 2023 manifest plus one arrival-boundary day transfers
about **283.93 GiB** across 185 daily inputs; its largest day is **1.81 GiB**.
Only the current day needs raw working space. A relaxed July 10, 2023 pilot
reconstructed **21,048 candidates** from a **1.13 GiB** archive containing 42,846
aircraft-days: 1,322 had ground endpoints and 19,726 used airborne proximity.
Under the earlier blanket estimate exclusion, **287** candidates were searchable
and **20,761** awaited review. The ingestion database occupied about **31.5 MiB**, including airport metadata,
receipts and overnight summaries. These historical admission counts predate the
current labeled-estimate policy and do not measure complete flight coverage.

A limited independent comparison against the official
[BTS July 2023 reporting-carrier file](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2023_7.zip)
found 4,992 matching airport pairs and 375 disagreements among comparable numeric
callsigns for nine US carrier prefixes. Most disagreements were proximity
estimates; some ground candidates crossed unobserved stops. This check does not
verify tail identity, timestamps, types or worldwide recall. It motivated the
earlier blanket review gate, rather than a claim of lossless extraction. Under that earlier gate,
all 375 disagreements were retained for review; 70 comparable searchable records
agreed and one had no reference match. This small subset does not establish
global accuracy. The current labeled-estimate admission intentionally accepts
some airport-assignment risk; those earlier agreement results do not establish
accuracy for the expanded searchable set.

Under the older strict extractor, a historical December 30, 2025 pilot
processed 2.98 GiB/61,215 aircraft-days into 2,138 accepted legs across 39 types.
Its earlier JSON representation used about 504 bytes/leg with the identity index;
a compacted typed-fact probe used about **244 bytes/leg** with the identity and two
observed-time indexes. Aircraft-days and flight-leg counts are not comparable.

At 244 bytes/leg, 20 GiB would hold roughly 88 million fact rows before metadata:
about 4.8 years at 50,000 legs/day or 2.4 years at 100,000. This is a fact-only
estimate. In a pre-reset live sample, 3,783 accepted legs over 69 days occupied
0.93 MiB of facts/indexes, while overnight summaries/index occupied 58.3 MiB.
Boundary state, diagnostics, airport metadata and WAL materially affect capacity.
Validate coverage first; compact boundary state without breaking disjoint backfills
before extrapolating whole-database storage or ingesting more years.

