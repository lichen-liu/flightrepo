# Flight data sources and verification methods

Research checked **2026-10-15, America/Toronto**. Measurements and access tests
below are dated findings, not guarantees of current availability. Only ADSB.lol,
OurAirports and VRS are implemented; other entries are research, not dependencies.

## Current selection: historical operated flights only

The implemented pipeline handles historical operated-flight observations:

| Role | Selected source | What it can establish |
| --- | --- | --- |
| Main historical itinerary and aircraft type | ADSB.lol daily archives | Best-effort observed/inferred legs, with retained bounds, uncertainty and observed type |
| Automatic route verification signal | VRS CC0 standing data | Undated callsign/adjacent-airport association; route plausibility, not proof of historical operation |
| Airport identity and names | OurAirports | IATA/ICAO names, geography, coordinates/elevation; timezone enrichment is tracked in [#21](https://github.com/lichen-liu/flightrepo/issues/21) |
| Operator names | VRS airline/operator CSV | Undated ICAO-to-name reference; does not establish the historic or marketing carrier |
| Requested timing reference | Flight.info | A date-valid historical timetable could support **scheduled** timing; tested current/upcoming pages do not supply 2023 actual timing |
| Requested actual timing reference | Flightradar24 | Needs permitted access, retention rights and the exact historical instance; no free persistent automated path has been established |

**Future scheduled-flight ingestion is deferred.** Do not expand upcoming timetable
periods into our serving database or add future dates to its calendar. The future
source research below is retained for reference, not the current implementation plan.
Existing scheduled/actual/observed fields remain distinct: a historical scheduled
time must not be presented as takeoff/landing or replace the observed uncertainty.

[Producer commands](ingestion.md#reference-catalogs-and-vrs-audit) import and audit
VRS locally. Undated route matches provide confidence signals, not proof of an
operation or exact timing. Search admission and public disclosure are defined in
[the schema/API contract](database-schema.md), independently of source coverage.
Flight.info/FR24 extraction is disabled until historical access and retention rights
are established.

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
| [ADSB.lol historical archives](https://www.adsb.lol/docs/open-data/historical/) | Daily release archives; ODbL-1.0; no per-flight key | Observations yield best-effort inferred legs, not independent proof of their endpoints | Past observations only; retain available observed aircraft type | Implemented daily extraction; [pipeline](ingestion.md) |
| [BTS reporting-carrier on-time data](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ) | Public federal monthly ZIP/CSV; source notices retained | Dated US reporting-carrier flights, identifiers, tail and operating times; includes cancellation/diversion evidence | Historical only; no per-flight aircraft type field | Existing limited comparison; not a new installed source |
| [VRS standing data](https://github.com/vradarserver/standing-data), [ADSB.lol mirror](https://github.com/adsblol/vrs-standing-data) | Whole CSVs or Git snapshot; CC0-1.0 | Undated callsign-to-airport-chain plausibility; current associations can disagree with historical operations | No service dates, departure/arrival times or aircraft assignment | Implemented: private import, daily backend audit, public accuracy summaries and shared carrier catalog |
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
then-held matches were measured under an earlier admission policy; this read-only
comparison changed no records. They are not current hold counts. An undated match
cannot rule out an intermediate stop during missing observations.

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
Its T7W/T7Z/T8H configuration values are not ICAO aircraft type codes.
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
Earlier endpoint tests and the October 15 probes are distinguished below; an
anonymous HTTP 200 establishes technical access, not unlimited API use.

| Source | Collection and future coverage | Free/unlimited verdict and evidence |
| --- | --- | --- |
| [Flight.info](https://www.flight.info/about), [BA117 example](https://www.flight.info/BA117) | One flight-code page contains dated recurrence periods and upcoming departures; airport/airline pages aid discovery. Advertises up to one year; example has an end date of 2027-10-12. | Public HTML, no documented free bulk export/API or open reuse grant found. Direct HTTP previously 403; browser route access is a separate capability. |
| [FlightsFrom](https://www.flightsfrom.com/en/about-us) | Airport/nonstop route pages, future calendar and route-level equipment; advertised horizon up to nine months. | Normal browser YYZ–SEA read succeeded. Not an unlimited export; aircraft FAQ conflicts with its equipment section. Route-level equipment is not a particular flight's actual type. |
| [FlightRoutes](https://www.flightroutes.com/about) | Direct and connecting options; SHOW SCHEDULE exposed an upcoming dated AC543 timetable in our browser test. | Normal browser works despite previous direct HTTP 403. No documented public bulk API/open reuse grant established. A connecting journey cannot validate a single nonstop leg. |
| [Directflights](https://www.directflights.com/about), [Flightinformation](https://www.flightinformation.com/about) | Related route/status sites, useful review references. | Westcoast source family, not independent confirmation votes; no unrestricted historical bulk feed established. |
| [FlightMapper](https://info.flightmapper.net/flight/British_Airways_BA_117), [disclaimer](https://info.flightmapper.net/disclaimer) | One HTML page contains several effective periods, weekdays and equipment. Direct GET succeeded (69,380 bytes). | Schedules are supplied by Cirium. No open mirror licence or documented batch/export API found. Open-ended effective periods must not be expanded indefinitely. |
| [FR24 website endpoint](https://api.flightradar24.com/common/v1/flight/list.json?query=BA117&fetchBy=flight&page=1&limit=2), [official API terms](https://www.flightradar24.com/terms-of-service) | Earlier test observed anonymous JSON with several instances of one code and near-future departures; not a multi-code database dump. | Undocumented website interface has no verified unlimited/reuse contract. Official metered API terms also restrict storage and backfilling other providers. Do not base our permanent mirror on it. |
| [AirLabs Routes](https://airlabs.co/docs/routes) | Airport/airline filters, pagination; recurring route times/weekdays and last-used type. | Free keys have 50-row pages and limited free fields; page size is not monthly quota. No unlimited authenticated test. Recurrence alone does not confirm a dated future departure. |
| [AirLabs Schedules](https://airlabs.co/docs/schedules) | Airport/airline collections with pagination; only up to ten hours ahead. | Free keys capped at 50 results/page; not an unrestricted long-range schedule source. |
| [FlightAware AeroAPI](https://www.flightaware.com/commercial/aeroapi/) | Supported airport/date collections and future schedules; earlier research reports up to a year. | Metered 15-record result sets and tier/storage rules. Small personal credit is not unlimited bulk access. Schedules currently listed at $0.020/result set. |
| [Aviationstack](https://aviationstack.com/pricing) | Airport/date future schedule collections; earlier research reports up to twelve months. | Free plan has 100 requests and real-time/personal-use scope; future flight listed on paid tiers. Excluded from no-metered-limit design. |
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

## Evidence and matching rules

- Match historical timing only to the same operation date, directed leg, operating
  identity and unique instance. Reused numbers, codeshares, midnight crossings and
  same-day repeats must not collapse into one operation.
- Keep scheduled, estimated, gate and takeoff/landing times distinct. Preserve local
  service date, day offset and IANA zone for that date. A current timetable cannot
  fill old actual timestamps; missing data stays unknown.
- Retain the observed aircraft type. Planned equipment, fleet family, registry
  model and actual per-flight type are different assertions.
- Route associations and plausible duration do not establish continuous flight,
  exact timing or human verification. Source-specific corrections need validated
  matching/provenance before writes.
- Reusable bulk files avoid per-record API quotas, not provider traffic limits.
  Cache, back off and honor terms. Reference downloads stay bounded in memory;
  ADSB archives use one-day RAM scratch and durable DB-only quotas.

[Measured probe evidence](research/flight-data-probes-2026-10-15.json) records tested
URLs, fields and sizes. [The ingestion guide](ingestion.md) is the operational
reference; the source/API models remain distinct.

## Additional researched options

The earlier October 13–15 inventory adds these findings. Registered/permissioned
endpoints were not authenticated unless explicitly stated; older quotas and
horizons need rechecking before implementation.

| Historical/regional source | Measured finding | Limitation or decision |
| --- | --- | --- |
| [BTS reporting-carrier OTP](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ) | July 2023 fully counted: 601,866 rows; 30,208,166-byte ZIP; 272,552,351-byte CSV; 14,606 cancelled, 2,202 diverted. Tail, no aircraft type. | Strong dated US reporting subset; carrier/local-date/time alignment required; do not invent a CC0 grant. |
| [BTS marketing-carrier OTP](https://transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGK) | Codeshare-partner reporting from 2018; different operating coverage. | Compare with reporting-carrier data; do not count marketing codeshares as extra physical flights. |
| [Brazil ANAC VRA](https://siros.anac.gov.br/siros/registros/diversos/vra/2023/), [official metadata](https://www.anac.gov.br/acesso-a-informacao/dados-abertos/areas-de-atuacao/voos-e-operacoes-aereas/voo-regular-ativo-vra/62-voo-regular-ativo-vra) | July: 87,318 rows; 82,831 operated; 48 nonempty equipment values. July–December files listed. Metadata specifies Brasília time, including foreign airports. | Equipment-bearing historical candidate; dataset reuse terms and planned/actual equipment semantics unresolved. Do not interpret timestamps as each airport's local time. |
| [Air France/KLM](https://transport.data.gouv.fr/datasets/programme-des-vols-air-france?locale=en) | 2023 SSIM archives exist. Earlier range probe measured a 58,659,840-byte file; 2 MiB sample had 1,046 templates overlapping target dates. Current portal still says licence unspecified. | Promising large native schedule source, conditional on reuse rights. A sample count is not full-file/daily-flight coverage. |
| [Corsair](https://transport.data.gouv.fr/datasets/programme-corsair-au-30-juin-2025) | SSIM archive listings from 2024; licence unspecified. | No proven 2023 source or unrestricted reuse grant. |
| [Argentina ANAC movements](https://datos.transporte.gob.ar/dataset/21038a1a-c3c7-4494-b76a-3a2a8fbb83b5) | 2023 file metadata: 62,735,708 bytes, CC BY4.0. Earlier 32 KiB sample shows takeoff/landing events, airports, operator/model, but no flight number/registration or both endpoint times. | Good movement benchmark; pairing repeated events into a unique complete leg remains unresolved. |
| [South Korea Airportal/MOLIT](https://www.data.go.kr/data/15092485/fileData.do) | Free metadata advertises date/airport/airline queries and Excel exports; departure-board columns inspected. | No actual July–December2023 export or complete endpoint/type coverage verified. |
| [Finnair timetable](https://www.finnair.com/jp-ja/timetables) | Official route calendar and planned aircraft details; includes partner-operated AY marketing flights. | Consumer route interface, not a verified free bulk export/reuse feed. |

| Registered/rolling source | Earlier finding | Limitation |
| --- | --- | --- |
| [Lufthansa OpenAPI plan](https://developer.lufthansa.com/product), [schedule endpoint](https://developer.lufthansa.com/docs/read/api_details/operations/Flight_Schedules) | Free registered plan; 6 calls/second, 1,000/hour; documented operations schedules to360 days. | Carrier/pair/date scope, account/terms; unrestricted full schedule listing not established. No authenticated payload tested. Status docs give conflicting past limits; no deep historical claim. |
| [Swedavia](https://apideveloper.swedavia.se/faq), [guide](https://apideveloper.swedavia.se/content/Using_the_FlightInfo_API.pdf) | Earlier documentation: free key tier around10,000 calls/month, seven days back/90 ahead. | Quota-based, not unlimited; no authenticated coverage/retention probe. |
| [Finavia portal](https://apiportal.finavia.fi/), [website coverage](https://customerservice.finavia.fi/en/support/solutions/articles/79000119961-where-can-i-find-information-on-flight-schedules-) | Free API advertised; Helsinki passenger site separately describes two future months. | Website horizon is not proof of API entitlement. No registered probe. |
| [Hong Kong Airport](https://data.gov.hk/en-data/dataset/aahk-team1-flight-info) | Prior exact-date probe:2023-07-10→400; previous-day2026 control→200. | Historical feed is previous-day/limited, not unlimited history or proved future schedules; no type/opposite-event time. |
| [Lyon departures](https://www.data.gouv.fr/datasets/vols-au-depart-de-laeroport-lyon-saint-exupery-disponibilites-temps-reel) | Airport rolling operational events; earlier portal window day−3 to+1, labelled public domain. | No2023 archive or complete-leg/type guarantee. |
| [Taiwan TDX realtime](https://data.nat.gov.tw/en/datasets/157198) | Free registered/key-based real-time feed, Open Government Data License1.0. | Separate from our newly tested no-key CAA CSV. Quota/history/schema not verified. |
| [Incheon T1 operations](https://www.data.go.kr/data/15160568/openapi.do) | Free approval-based API; earlier docs day−3 to+6, subtype/registration, status/codeshares. | No2023 backfill. Metadata and description have permission/alteration ambiguity; no payload tested. |
| [Japan ODPT](https://developer.odpt.org/terms/data_basic_use_guideline.html) | Transport feeds and basic data-use guidelines. | Dynamic validity/display and provider-specific terms; no2023 flight export verified. |
| [AirLabs historical beta](https://airlabs.co/docs/historical) | Known-flight lookup and pagination; some scheduled fields labelled free. | No confirmed2023 depth, unlimited quota, complete enumeration or permanent-storage grant. |
| [Amadeus developer portal](https://developers.amadeus.com/) | October13 research observed self-service decommissioning notice; older free-tier tutorials remain online. | Old free self-service onboarding is superseded; no new unrestricted schedule feed demonstrated. |

| Research/aggregate source | Useful information | Why it cannot populate complete dated itineraries |
| --- | --- | --- |
| [EUROCONTROL ADRR](https://www.eurocontrol.int/dashboard/aviation-data-research), [terms](https://www.eurocontrol.int/sites/default/files/2025-04/eurocontol-aviation-data-repository-research-terms-of-use.pdf) | European operational records and types under vetted R&D access. | Public display/derived-product transfer restricted; not our unrestricted public-serving feed. |
| [FAA ASPM / SWIM](https://www.aspm.faa.gov/) | Operational/planned data where access is granted. | Individual-flight reports are non-public/elevated-access in the reviewed portal. |
| [OpenSky](https://opensky-network.org/about/faq), [published flight lists](https://zenodo.org/records/7323875) | Precomputed inferred-flight datasets reduce trace processing. | Inferred endpoints, version-specific licences, no new verified free2023 schedule export; no future schedules. |
| [UK CAA punctuality](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/statistics-guide/) | Airline/route punctuality benchmarks. | Aggregate counts, not individual dated flight times; reuse/resale notices apply. |
| [Australia BITRE](https://data.gov.au/data/en/dataset/domestic-airline-on-time-performance) | Monthly carrier/route punctuality. | Aggregates, not dated itineraries. |
| [BTS T-100](https://transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=Nv4+Pn44vr45&gnoyr_VQ=GDM) | Monthly carrier/route/type totals. | Does not identify which aircraft type operated a particular OTP flight. |
| [Colombia Aerocivil](https://www.aerocivil.gov.co/atencion/estadisticas-de-las-actividades-aeronauticas/bases-de-datos) | Official operational/OD listings for2023 found. | Full dated-leg schema not obtained; reviewed open-data page mentioned noncommercial use. |
| [Singapore CAAS](https://data.gov.sg/datasets/d_744e62bfb1c524508bce0a64a2488243/view) | Open monthly traffic benchmarks. | Counts rather than individual flight legs. |

## Measured extraction and storage

The completed July–December 2023 extraction used **185 input/context days**, about
**283.93 GiB** transferred sequentially, largest preferred raw day **1.81 GiB**.
On October 16 the private store held **5,539,724** legs; serving held **5,539,206**
July–December departures across **158,474 directed pairs** and **2,312 airports**.
The 518 June 30 boundary records stayed private. Serving/private SQLite families
were about **3.85/5.99 GiB**, each under a separate 20 GiB quota. Sizes include
indexes/reference data and vary with WAL, checkpoints and later edits.

The shared catalog contained **8,801** named/geolocated airports and **5,965**
operator references. **4,374,105** serving records resolved an undated operator
name; **5,520,456** retained aircraft type. These counts describe best-effort
reconstruction, not certified commercial service or lossless coverage.

Earlier pilots are useful comparisons, not current admission instructions:

| Pilot | Input and output | Interpretation |
| --- | --- | --- |
| Relaxed July 10, 2023 | 1.13 GiB; 42,846 aircraft-days → 21,048 candidates (1,322 ground, 19,726 proximity); about 31.5 MiB retained | Under the old blanket estimate gate, 287 searchable / 20,761 review; not current policy |
| Strict December 30, 2025 | 2.98 GiB; 61,215 aircraft-days → 2,138 legs / 39 types | Aircraft-days and individual flights are not comparable; strict filtering omitted real legs |
| Typed-fact size probe | About 244 bytes/leg with source identity and two observed-time indexes, versus about 504 bytes for earlier JSON | Excludes boundaries, diagnostics, reference catalogs and WAL; not full-DB capacity |
| Pre-reset strict sample | 3,783 legs/69 days: 0.93 MiB facts/indexes plus 58.3 MiB boundaries/index | Boundary overhead can dominate small/strict samples |

At the fact-only 244 bytes/leg, 20 GiB is about 88 million rows (4.8 years at
50,000/day, 2.4 at 100,000/day). Whole-database retention is lower and depends on
actual extraction density and retained evidence. Use the measured full-store
sizes rather than presenting this estimate as a capacity promise.

A limited BTS July 2023 comparison for nine US numeric carrier prefixes found
**4,992 matching airport pairs and 375 disagreements**. Most disagreements were
proximity estimates; some ground candidates crossed unobserved stops. It did not
verify worldwide recall, tails, exact times or types. Its old searchable subset
had 70 agreements and one unmatched record, which does not establish expanded
search accuracy. Raw traces are discarded; improved extraction or a richer
rejection analysis needs those days downloaded again. The January 2024 pilot
validated integrity, duplicates, time bounds, aircraft-type retention and consumer
access before the 2023 replacement; structural success is not completeness proof.
