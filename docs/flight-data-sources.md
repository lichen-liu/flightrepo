# Flight data sources and verification methods

Research checked **2026-10-15, America/Toronto**. This consolidates the supplied
research handoff and our access tests. It is a source-selection reference, not a
list of installed adapters. The [earlier source inventory](research/previous-flight-source-inventory.md) also
preserves regional, aggregate, research-only and gated options. The running
ingestion remains ADSB.lol-only plus the
existing airport reference. New probes neither publish flights nor change review
flags, human approvals or serving data.

## Current selection: historical operated flights only

The user selected this scope on October 15, 2026:

| Role | Selected source | What it can establish |
| --- | --- | --- |
| Main historical itinerary and aircraft type | ADSB.lol daily archives | Best-effort observed/inferred legs, with retained bounds, uncertainty and observed type |
| Automatic route verification signal | VRS CC0 standing data | Undated callsign/adjacent-airport association; route plausibility, not proof of historical operation |
| Requested timing reference | Flight.info | A date-valid historical timetable could support **scheduled** timing; tested current/upcoming pages do not supply 2023 actual timing |
| Requested actual timing reference | Flightradar24 | Needs permitted access, retention rights and the exact historical instance; no free persistent automated path has been established |

**Future scheduled-flight ingestion is deferred.** Do not expand upcoming timetable
periods into our serving database or add future dates to its calendar. The future
source research below is retained for reference, not the current implementation plan.
Existing scheduled/actual/observed fields remain distinct: a historical scheduled
time must not be presented as takeoff/landing or replace the observed uncertainty.

[VRS import and reporting](vrs-verification.md) is implemented in the private
producer tools. It never changes endpoints, times, aircraft types, admission or
human approvals. Flight.info/FR24 timing extraction is not enabled: historical
coverage, matching and access constraints still need resolution. The ADSB job and
previously approved adoption/audit/publication workflow can proceed independently.

Flight.info's [own description](https://www.flight.info/about) describes upcoming
services and monthly updates; it does not establish a 2023 historical archive.
Flightradar24's [current terms](https://www.flightradar24.com/terms-of-service)
§2.3 restrict automated website retrieval, §2.4/§6.3 restrict stored API copies,
and §6.3.1 addresses supplementing other providers. Its undocumented website JSON
is not a supported unlimited historical-timing feed. A paid API key alone would
not resolve persistent reuse for this database. No automated website scraper or
FR24 backfill has been installed.

Here, *free without a metered API quota* means downloading reusable published
files and doing subsequent work locally. It does not mean unlimited traffic to
someone else's server. Respect caching, backoff and provider refresh intervals.
Batch lookup, paginated collection and whole-database download are different.

## Researched sources (selection above takes precedence)

| Source and primary documentation | Bulk access / licence | Historical validation | Future schedule / aircraft information | Status |
| --- | --- | --- | --- | --- |
| [ADSB.lol historical archives](https://www.adsb.lol/docs/open-data/historical/) | Daily release archives; ODbL-1.0; no per-flight key | Observations yield best-effort inferred legs, not independent proof of their endpoints | Past observations only; retain available observed aircraft type | Running one raw day at a time; [pipeline](ingestion.md) |
| [BTS reporting-carrier on-time data](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ) | Public federal monthly ZIP/CSV; source notices retained | Dated US reporting-carrier flights, identifiers, tail and operating times; includes cancellation/diversion evidence | Historical only; no per-flight aircraft type field | Existing limited comparison; not a new installed source |
| [VRS standing data](https://github.com/vradarserver/standing-data), [ADSB.lol mirror](https://github.com/adsblol/vrs-standing-data) | Whole CSVs or Git snapshot; CC0-1.0 | Undated callsign-to-airport-chain plausibility; current associations can disagree with historical operations | No service dates, departure/arrival times or aircraft assignment | Whole route CSV downloaded and local comparison demonstrated |
| [OurAirports](https://ourairports.com/data/) | Whole CSVs; public domain | Airport identity, coordinates and elevation for geometric checks | Airport metadata only; no schedules or reliable flight-specific timezone offsets | Existing airport reference |
| [FAA aircraft register](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download) | Public daily bulk archive with master and make/model reference files | Possible US registration-to-model enrichment; ownership/registration change over time | Registry identity only; not a flight or schedule source | Documentation checked; no new registry import |
| [Taiwan CAA international/cross-strait timetable](https://data.gov.tw/en/datasets/9973) | One CSV; free; Open Government Data License 1.0, [terms](https://data.gov.tw/license) | A saved date-valid edition can support planned-service evidence; not proof of actual operation | Recurrence periods, both local times and planned equipment; Taiwan-related international/cross-strait scope | Live whole-file download and date expansion demonstrated |
| [TUI](https://transport.data.gouv.fr/datasets/programme-de-vols-tui-fly-belgium-s25-ssim) / [Air Corsica](https://transport.data.gouv.fr/datasets/programme-des-vols-air-corsica) | Whole SSIM; Open Licence 2.0 | TUI has a tested summer 2023 archive; Air Corsica listed archives begin in 2024 | Current summer 2026 native schedule periods and planned equipment | Current files downloaded and inspected; do not assume winter renewals |
| [Finist’Air](https://transport.data.gouv.fr/datasets/finistair-region-bretagne?locale=en) | Whole GTFS ZIP; ODbL and linked specific conditions | Historical archive depth unverified | Brest–Ouessant schedule; current edition through December 31, 2026; no aircraft type or public flight number | Live download and calendar-exception expansion demonstrated |
| [Avinor flight data](https://partner.avinor.no/tjenester/flydata/) | Anonymous airport XML collections; provider reuse terms and visible linked attribution | Preserve snapshots going forward; not a 2023 archive | Near-future arrival/departure events; no documented aircraft type field or paired opposite-event time | Live OSL departures collection demonstrated |
| [HK Airport open data](https://data.gov.hk/en-data/dataset/aahk-team1-flight-info) | Documented JSON collection; check dataset-owner terms | The listed feed explicitly provides previous-day history | Do not confuse this historical feed with a verified future timetable API | Documentation only; not selected for future schedules |
| [Schiphol developer platform](https://developer.schiphol.nl/) | Registered API access; current quotas/entitlements must be verified | Airport-scoped flight information | Potential schedules/status; not a demonstrated anonymous unlimited dump | October 2026 platform/JWT migration; no account or authenticated probe |
| [OpenSky](https://opensky-network.org/about/faq) | Credit-limited REST; research access/public datasets have separate conditions | Track-derived airports, not independent itinerary truth; consent required for commercial API use | Explicitly does not provide commercial schedules | Excluded as a solution to free future schedules |
| [OpenFlights](https://openflights.org/data.php) | Bulk static data; ODbL/contents notices, component qualifications | Route feed stopped updates in June 2014 | No current timetable service; plane-code mappings can aid normalization with provenance | Not a current flight-validation or future-schedule source |

### Proven route-reference download

```text
https://vrs-standing-data.adsb.lol/routes.csv
https://vrs-standing-data.adsb.lol/airports.csv
```

The tested route CSV is 18.08 MiB, not raw traces. It contains 586,173 two-airport
chains and 34,527 longer chains. Match a **consecutive pair** for a leg; the first
and last airports of a multi-stop chain do not imply a nonstop service. There are
no validity dates or aircraft types. Preserve the snapshot hash and retrieval time.

The mirror advertises hourly refresh; our response's Last-Modified was September
20, 2026. Do not turn that advertised cadence into a freshness guarantee. Upstream
Git snapshots can be pinned; historical snapshot availability and date validity
must be checked before claiming a reference applies to 2023.

[VRS's normalization specification](https://github.com/vradarserver/standing-data/blob/main/routes/schema-01/README.md)
uses airline-code mappings and number normalization. Apply those only with explicit
reference mappings; keep the original callsign. A missing association is unknown,
not grounds to restore an operator whitelist or delete a candidate.

A read-only comparison against legs whose whole departure interval falls on
**July 10, 2023 UTC** found:

| Result using exact trimmed/uppercased callsign | All legs | Held legs |
| --- | ---: | ---: |
| Exact two-airport association | 5,839 | 3,676 |
| Consecutive leg in a longer reference chain | 446 | 301 |
| Different current route association | 9,866 | 7,047 |
| No callsign reference | 6,261 | 3,021 |
| Total | 22,412 | 14,045 |

These demonstrate efficient offline matching, not validation accuracy. For example,
a current DAL1980 association differs from one historical DAL1980 leg. Callsign
reuse, route changes and inference errors can all produce disagreement. The 3,977
held matches remain held: an undated route match cannot rule out an intermediate
stop during missing observations. No flight records were changed.

### Deferred research: regional future timetable

Stable discovery: [CAA dataset 9973](https://data.gov.tw/en/datasets/9973).
The advertised CSV currently resolves to:

```text
https://www.caa.gov.tw/FileAtt.ashx?id=16679&lang=1
```

Measured download: **133,668 bytes / 1,471 recurrence rows**, all with a primary
planned equipment code. The tested edition spans September 28–October 25, 2026.
Using October 15 as the local-date cutoff, 719 rows reach that date or later and
expand to **2,125 distinct departure keys through October 25**. Counts are schedule
expansion results, not confirmed operated flights or guaranteed seat availability.
No global or one-year horizon follows from this file.

Operating weekdays use a seven-position mask; nonzero positions identify days.
Times are local HHMM. Equipment values are schedule codes such as `321`, `77W` and
`32N`, not automatically ICAO codes. Preserve their original namespace and planned
status; convert only through an explicit unambiguous type map. Secondary equipment
and transit fields exist; a later edition may contain intermediate stops.

System curl downloaded this file with normal TLS verification. Python 3.14/httpx
rejected the server certificate under its stricter certificate handling. Do not
use insecure TLS flags as a fallback; test the selected deployment client's trust
handling. The catalogue's update timestamp is not a substitute for the CSV's
actual effective dates. Retain attribution required by the published licence.

### Deferred research: European seasonal bulk schedules

France's National Access Point links openly licensed airline-produced files:

| Feed downloaded | Bytes | Schedule templates | Actual effective range |
| --- | ---: | ---: | --- |
| TUI current SSIM | 96,960 | 140 type-3 records; 50 still reach October 15 | March 29–October 24, 2026 |
| Air Corsica current SSIM | 477,528 | 2,349 type-3 records; 328 still reach October 15 | March 29–October 24, 2026 |
| Finist’Air GTFS | 2,490 | Six trip templates; 195 future departures after calendar exceptions | Tested future departures October 15–December 31, 2026 |

Type-3 counts are templates, not expanded daily flights. TUI's catalogue filename
still says S25 while its downloadable file is S26: inspect effective dates, not
labels or modification timestamps. Both SSIM datasets specify Open Licence 2.0.
Current editions only prove the dates inside them; no winter 2026/27 renewal was
found for these two carriers. SSIM supplementary records, offsets, operating
suffixes and codeshares need a real format parser before publication.

Finist’Air is just one small regional route with two stops. Its GTFS has an agency
IANA timezone and both endpoint times, but no equipment/public flight number.
Treat those as unknown. Apply service-day exceptions, not merely the calendar's
weekday flags. Its ODbL and specific linked usage conditions must be retained.

The older tested TUI 2023 archive has 1,802 dated rows / 118,615 bytes, including
1,092 in July–October. These are published schedules, not operated-flight proof.
Air France/KLM has much larger SSIM archives, but the portal currently lists its
licence as unspecified; it is not promoted to the unrestricted shortlist.

### Deferred research: short-horizon operational feed

```text
https://asrv.avinor.no/XmlFeed/v1.0?TimeFrom=1&TimeTo=7&airport=OSL&direction=D
```

A normal GET returned **71 departure events / 19,743 bytes**. The default future window is seven **hours**. The
[current public specification](https://xwiki.avinor.no/spaces/AASRV/pages/171475934/XmlFeed-%2BApplication%2Bdocumentation)
allows 36 hours back and **336 hours / 14 days ahead**. A second GET using those
documented limits returned **4,493 departure events / 972,128 bytes**, extending
to October 29, 2026 UTC. Parameter limits are not completeness guarantees. Norway airport scope excludes private
operators' airports. Cache on the backend and follow the recommended refresh
cadence; heavy usage requires contacting the provider. Display the prescribed
linked Avinor attribution. Missing equipment/opposite-event times stay unknown;
this feed alone cannot populate a complete searchable international itinerary.

## Browser-readable sources and commercial APIs

All entries below remain researched alternatives, not installed production feeds.
The pasted handoff's successful endpoint tests are marked separately from our new
probes; an anonymous HTTP 200 establishes technical access, not unlimited API use.

| Source | Collection and future coverage | Free/unlimited verdict and evidence |
| --- | --- | --- |
| [Flight.info](https://www.flight.info/about), [BA117 example](https://www.flight.info/BA117) | One flight-code page contains dated recurrence periods and upcoming departures; airport/airline pages aid discovery. Advertises up to one year; example has an end date of 2027-10-12. | Public HTML, no documented free bulk export/API or open reuse grant found. Direct HTTP previously 403; browser route access is a separate capability. |
| [FlightsFrom](https://www.flightsfrom.com/en/about-us) | Airport/nonstop route pages, future calendar and route-level equipment; advertised horizon up to nine months. | Normal browser YYZ–SEA read succeeded. Not an unlimited export; aircraft FAQ conflicts with its equipment section. Route-level equipment is not a particular flight's actual type. |
| [FlightRoutes](https://www.flightroutes.com/about) | Direct and connecting options; SHOW SCHEDULE exposed an upcoming dated AC543 timetable in our browser test. | Normal browser works despite previous direct HTTP 403. No documented public bulk API/open reuse grant established. A connecting journey cannot validate a single nonstop leg. |
| [Directflights](https://www.directflights.com/about), [Flightinformation](https://www.flightinformation.com/about) | Related route/status sites, useful review references. | Westcoast source family, not independent confirmation votes; no unrestricted historical bulk feed established. |
| [FlightMapper](https://info.flightmapper.net/flight/British_Airways_BA_117), [disclaimer](https://info.flightmapper.net/disclaimer) | One HTML page contains several effective periods, weekdays and equipment. Direct GET succeeded (69,380 bytes). | Schedules are supplied by Cirium. No open mirror licence or documented batch/export API found. Open-ended effective periods must not be expanded indefinitely. |
| [FR24 website endpoint](https://api.flightradar24.com/common/v1/flight/list.json?query=BA117&fetchBy=flight&page=1&limit=2), [official API terms](https://www.flightradar24.com/terms-of-service) | Supplied handoff observed anonymous JSON with several instances of one code and near-future departures; not a multi-code database dump. | Undocumented website interface has no verified unlimited/reuse contract. Official metered API terms also restrict storage and backfilling other providers. Do not base our permanent mirror on it. |
| [AirLabs Routes](https://airlabs.co/docs/routes) | Airport/airline filters, pagination; recurring route times/weekdays and last-used type. | Free keys have 50-row pages and limited free fields; page size is not monthly quota. No unlimited authenticated test. Recurrence alone does not confirm a dated future departure. |
| [AirLabs Schedules](https://airlabs.co/docs/schedules) | Airport/airline collections with pagination; only up to ten hours ahead. | Free keys capped at 50 results/page; not an unrestricted long-range schedule source. |
| [FlightAware AeroAPI](https://www.flightaware.com/commercial/aeroapi/) | Supported airport/date collections and future schedules; supplied handoff reports up to a year. | Metered 15-record result sets and tier/storage rules. Small personal credit is not unlimited bulk access. Schedules currently listed at $0.020/result set. |
| [Aviationstack](https://aviationstack.com/pricing) | Airport/date future schedule collections; supplied handoff reports up to twelve months. | Free plan has 100 requests and real-time/personal-use scope; future flight listed on paid tiers. Excluded from no-metered-limit design. |
| [AeroDataBox](https://aerodatabox.com/pricing/) | Airport collections; future availability up to 180/365 days on listed paid tiers, coverage-dependent. | Monthly API-unit quotas and 7-day/extended caching terms vary by plan; permanent free mirror not established. |
| [Flightera](https://www.flightera.net/en/pricing) | Paid batch access and historical/schedule data. | Free: 1,000 credits, ten results/request, seven-day history, no full detail. Unlimited rows on a paid request still consume per-record credits. |
| [ADSBDB](https://github.com/mrjackwills/adsbdb) | Working callsign associations; aircraft metadata lookup; no dated timetable. | README explicitly restricts copying/publishing/incorporating route data without upstream permission. MIT software licence does not free the route dataset. |
| [HexDB](https://hexdb.io/) | Per-callsign associations and aircraft lookup; no dated timetable. | Site explicitly asks users not to scrape its database; no verified bulk export/reuse grant. An old route update timestamp cannot establish 2023 validity. |
| [OAG file delivery](https://www.oag.com/file-based-schedule-data) / Cirium | Commercial global schedule feeds address the actual whole-dataset use case. | No free unrestricted dump established. Listed for future reference if the cost constraint changes. |

United is a useful **technical bulk-download example**, not an approved open feed:
its [official schedule page](https://www.unitedcargo.com/en/us/learn/schedules.html)
links full-network PDF/XLSX. We downloaded the 25-page, 1,014,833-byte October 2026
PDF with flight numbers, validity ranges, weekdays, both times and equipment
families. It updates monthly. Truck schedules are separate. Its
[published terms](https://www.unitedcargo.com/en/us/terms-and-conditions.html)
restrict copying, redistribution and software/database uses except where granted;
public download access does not establish our required reuse rights. Keep it out
of the unrestricted-feed shortlist until those rights are resolved.

## Selected pipeline and timing rules

```text
ADSB.lol daily observations ────────────────┐
OurAirports airport reference ──────────────┼─> private ingestion SQLite
VRS route snapshot ────────────────────────┘       ├─ retained itinerary facts
                                                  ├─ undated route-check reports
Permitted date-specific historical timing ─ ─ ─ ─>├─ separate timing assertions
    (Flight.info/FR24 access not yet established)  └─ audited eligible facts
                                                         ↓ explicit publish
                                                   serving SQLite
```

Only ADSB/airport extraction is running in the legacy job. VRS is a separate private
reference importer, to run after adoption; it is not inserted into the active legacy
writer. No future schedule importer is selected. BTS and all other sources above
remain documented alternatives, not additional active dependencies.

1. **Route checks:** use exact trimmed/uppercased callsigns and explicit airport-code
   mappings. Check adjacent directed pairs in multi-stop associations. Report a
   match, disagreement, missing callsign or incomplete mapping separately. Current
   disagreement cannot invalidate an old flight; missing coverage is unknown.
2. **Historical timing matching:** require the same operation date, directed leg,
   operating identity and a unique candidate. Use aircraft identity where available;
   code shares, reused numbers, same-day repeats and midnight crossings must not be
   collapsed. A current recurring timetable cannot fill an old flight's timestamps.
3. **Timing semantics:** record scheduled, estimated and actual separately. Distinguish
   gate-out/in from takeoff/landing; preserve timezone, local service date, day offset,
   source URL, source instance and retrieval time. Use IANA timezone rules for the
   flight date, not today's offset. Do not invent exact times from observed bounds.
4. **Corrections:** keep conflicting, ambiguous or unavailable timing evidence private.
   A VRS match or scheduled duration cannot remove an observation-gap/timing hold,
   prove no intermediate stop, or mark a human approval. Preserve the two explicit
   single-record overrides. A source-specific actual-time correction needs its own
   validated matching and provenance policy before enabling writes.
5. **Aircraft types:** retain ADSB's observed type. Planned equipment, a fleet family
   and an actual per-flight ICAO type are different assertions; never overwrite the
   latter with a schedule guess.
6. **Storage and isolation:** bulk VRS CSVs are bounded in process memory, normalized
   into one atomic private reference snapshot, then discarded. Durable reference
   rows/receipts count toward the ingestion DB's own quota. They never enter serving.
   Raw ADSB archives continue one day at a time on the RAM volume; raw/staging space
   remains outside the DB quota. Admin only reads/queries or explicitly clears the
   private DB; source writes use the producer CLI/API.

## Next implementation order

1. Finish the active ADSB run, adopt its completed facts into the private ingestion
   SQLite and run the retained-fact audit. Preserve source/rollback and serving data.
2. Run the tested [VRS bulk import and cohort report](vrs-verification.md) against
   that private store; use its report to prioritize review. No blanket hold release.
3. Resolve Flight.info historical-date availability and reusable timing access;
   resolve FR24 API/retention and historical coverage before any connector is enabled.
   Keep unsupported timestamps unknown. Neither is currently a proven free unlimited
   source for permanently enriching July–December 2023 or January 2024.
4. Publish eligible historical ADSB facts with existing estimate labels through the
   separate validated serving workflow. Do not block it on optional timing sources
   or add future scheduled facts as a substitute.

## Evidence and repeatability

[Measured probe summary](research/flight-data-probes-2026-10-15.json) records byte
counts, hashes, schemas, date expansion and local-match counts. The supplied
handoff's attachment hash is recorded there; its one-off endpoint observations
remain separate from tests repeated here. Temporary raw downloads were on the RAM
disk and are not committed. Discovery URLs are preferred over expiring asset IDs.
Recheck prices, licences, file effective dates and platform migrations before
implementation; a no-key endpoint is not a promise of unlimited availability.
