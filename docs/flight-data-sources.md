# Flight data sources and verification methods

Research checked **2026-10-15, America/Toronto**. This consolidates the supplied
research handoff and our access tests. It is a source-selection reference, not a
list of installed adapters. The [earlier source inventory](research/previous-flight-source-inventory.md) also
preserves regional, aggregate, research-only and gated options. The running
ingestion remains ADSB.lol-only plus the
existing airport reference. New probes neither publish flights nor change review
flags, human approvals or serving data.

## Decision

- **Free bulk historical ingestion:** keep ADSB.lol daily archives. Use BTS monthly
  files for independent, dated validation within US reporting-carrier coverage.
- **Free bulk route plausibility:** download VRS standing data once and match
  locally. The tested ADSB.lol mirror contains 620,700 callsign-route associations
  in 18,957,159 bytes. Its CC0 data is different from ADSBDB's restricted route data.
- **Free, reusable future schedules:** Taiwan CAA has an openly licensed whole-file
  timetable with dates, weekdays, times and planned aircraft codes. A live download
  worked. Coverage is regional and the tested edition ends October 25, 2026.
- **Free European seasonal timetables:** TUI and Air Corsica publish open-licensed
  SSIM files; both current downloads worked. Their tested summer editions end
  October 24, 2026. Finist’Air publishes a small GTFS file through December 31.
- **Free near-future operational evidence:** Avinor's documented, anonymous XML
  collections work, with attribution and caching conditions. Each flight entry
  describes an arrival or departure event, not necessarily a complete itinerary.
- **Broad future schedule pages:** Flight.info, FlightMapper, FlightsFrom and
  FlightRoutes are technically readable. No unlimited bulk export or open mirror
  licence was established. Keep these as conditional adapters, not the foundation
  of an unrestricted public schedule database.

**No tested combination supplies a complete worldwide future schedule database,
with exact aircraft type per flight, under unrestricted free bulk reuse.** An
open-data combination works with explicit regional/date coverage. Browser access
alone does not resolve coverage, stability or database-reuse rights.

Here, *free without a metered API quota* means downloading reusable published
files and doing subsequent work locally. It does not mean unlimited traffic to
someone else's server. Respect caching, backoff and provider refresh intervals.
Batch lookup, paginated collection and whole-database download are different.

## Primary sources and open bulk references

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

### Proven regional future timetable

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

### Proven European seasonal bulk schedules

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

### Proven short-horizon operational feed

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

## Combination that fits the current schema

```text
ADSB.lol daily observations ────────────────┐
BTS dated historical facts (US scope) ──────┤
VRS undated route associations ─────────────┤
OurAirports / permitted type references ────┼─> private ingestion SQLite
CAA / open airline schedule periods ───────┤       ├─ retained source assertions
Avinor short-horizon events ────────────────┘       ├─ grouped consistency results
                                                   └─ eligible complete facts
                                                           ↓ explicit publish
                                                     serving SQLite
```

This is a proposed adapter combination, not a change to the running ADSB job.

1. **Historical validation:** retain ADSB observed bounds/type; compare a BTS
   record only with compatible date, identity, operating carrier and route/time
   evidence. Match local departure dates with real timezones, and preserve
   cancellations/diversions. Do not treat absent US coverage as a negative vote.
   VRS supplies an inexpensive extra route-association signal, not dated proof.
2. **Future schedules:** expand only explicit effective periods and weekdays from
   openly reusable timetable files. Preserve marketing/operating identifiers,
   stop sequence, local time, timezone, arrival-day offset and original equipment
   code. A later update can alter/cancel service. No extrapolation beyond expiry.
3. **Time correctness:** use maintained IANA timezone rules for each airport/date;
   do not copy a webpage's current UTC offset or assume an arrival date from clock
   order alone. If required timing/type specificity is unsupported, retain unknowns
   in private storage rather than fabricate a searchable complete leg.
4. **Aircraft types:** observed ADSB type, registry model and planned schedule
   equipment are different assertions. A registry join requires the same aircraft
   identity and temporal validity. An aircraft family label must not become a
   specific ICAO subtype by guesswork. Never overwrite actual type with planned.
5. **Identity and evidence:** keep the existing unique source/native key and
   revision controls. A schedule-period identity includes source, operating flight,
   leg, validity, weekday pattern and local times. Cross-source matching is not
   permission to merge distinct code shares or physically different operations.
6. **Validation states:** distinguish dated-actual support, dated-planned support,
   undated route match, conflict, unknown, stale and access failure. Keep geometric
   timing checks and observation-gap holds; neither a route match nor a schedule
   proves the aircraft made no unseen stop. Preserve both explicit human overrides.
7. **Storage:** download bounded source files into RAM, normalize once, remove raw
   responses, and keep compact source/version/effective-date assertions in ingestion
   SQLite. Use atomic snapshot replacement, hashes/conditional GET and resumable
   jobs. Serving gets admitted facts and attribution, never private evidence/raw
   responses. Each DB owns its DB/WAL/SHM quota; raw RAM and staging are excluded.

Existing `ingestion_documents` supports bounded reference assertions, not a proven
620,700-row production ingestion adapter. Implement a versioned bulk importer and
bounded transaction/recovery strategy before loading a whole reference snapshot.
Do not route producer writes through the read/query/reset-only admin interface.

## Next implementation order

1. Add a VRS **private reference** importer and cohort report; evaluate against
   retained July 10 examples and, separately, dated BTS evidence. Publish no new
   human-verification state and do not change admission on an undated match.
2. Add schedule-period importers for **CAA plus open TUI/Air Corsica SSIM**, and
   optionally the small Finist’Air GTFS. These are proven no-key, whole-file future
   sources. Validate timezone/day-offset and equipment mappings, codeshares,
   overlapping periods, deduplication, withdrawal and publication in a disposable
   private/serving pair before enabling it. Expose the actual regional/date coverage.
3. Add Avinor for near-departure status/reference where useful; it is incomplete
   itinerary evidence unless the opposite event is supplied by another source.
4. Keep Flight.info/FlightMapper/browser adapters optional until reuse/refresh and
   historical-date coverage are established. No account creation, paid subscription,
   outreach or mass scrape was performed during this research.

## Coverage-first choice

For the strict zero-payment/no-metered-API requirement, choose **ADSB + BTS + VRS**
for historical evidence and scoped validation, and **CAA + open airline SSIM/GTFS**
for future scheduled legs. Add Avinor's 14-day collections where event completeness
can be established. This is implementable regional coverage, not worldwide
completeness. The longest tested future file here is a tiny December 31 GTFS;
this does not imply broad airline/type coverage to that date.

If the requirement changes to allow a free key with rate limits, Lufthansa's
[open plan](https://developer.lufthansa.com/product) and
[schedule endpoint](https://developer.lufthansa.com/docs/read/api_details/operations/Flight_Schedules)
are a separate candidate: up to 360 days documented, 1,000 calls/hour and six/second.
An unrestricted whole-schedule listing is not included in that finding. No account
or authenticated payload was tested. This is not the no-limit bulk solution.

## Evidence and repeatability

[Measured probe summary](research/flight-data-probes-2026-10-15.json) records byte
counts, hashes, schemas, date expansion and local-match counts. The supplied
handoff's attachment hash is recorded there; its one-off endpoint observations
remain separate from tests repeated here. Temporary raw downloads were on the RAM
disk and are not committed. Discovery URLs are preferred over expiring asset IDs.
Recheck prices, licences, file effective dates and platform migrations before
implementation; a no-key endpoint is not a promise of unlimited availability.
