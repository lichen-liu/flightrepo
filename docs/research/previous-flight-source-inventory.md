# Earlier flight-source research retained for reference

Consolidated 2026-10-15 (America/Toronto) from the October 13 ingestion study,
October 15 itinerary-native investigation, expanded free-source inventory and
user-supplied API handoff. These are research findings, not installed adapters.
Use the [current decision and fresh probes](../flight-data-sources.md) first.
Previously tested measurements below were not all downloaded again in this run.

## Regional native records and timetable archives

| Source / primary reference | Retained finding | Use decision |
| --- | --- | --- |
| [BTS reporting-carrier OTP](https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ) | July 2023 fully counted: 601,866 rows; 30,208,166-byte ZIP; 272,552,351-byte CSV; 14,606 cancelled, 2,202 diverted. Tail, no aircraft type. | Strong dated US reporting subset; carrier/local-date/time alignment required; do not invent a CC0 grant. |
| [BTS marketing-carrier OTP](https://transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGK) | Codeshare-partner reporting from 2018; different operating coverage. | Compare with reporting-carrier data; do not count marketing codeshares as extra physical flights. |
| [Brazil ANAC VRA](https://siros.anac.gov.br/siros/registros/diversos/vra/2023/), [official metadata](https://www.anac.gov.br/acesso-a-informacao/dados-abertos/areas-de-atuacao/voos-e-operacoes-aereas/voo-regular-ativo-vra/62-voo-regular-ativo-vra) | July: 87,318 rows; 82,831 operated; 48 nonempty equipment values. July–December files listed. Metadata specifies Brasília time, including foreign airports. | Equipment-bearing historical candidate; dataset reuse terms and planned/actual equipment semantics unresolved. Do not interpret timestamps as each airport's local time. |
| [TUI Belgium](https://transport.data.gouv.fr/datasets/programme-de-vols-tui-fly-belgium-s25-ssim) | Historical summer 2023 CSV measured: 118,615 bytes, 1,802 dated rows, 1,092 July–October rows. Open Licence 2.0. Old configuration values T7W/T7Z/T8H are not ICAO types. | Free scheduled-leg backfill for its actual carrier/routes/season; current S26 file retested in the main reference. |
| [Air France/KLM](https://transport.data.gouv.fr/datasets/programme-des-vols-air-france?locale=en) | 2023 SSIM archives exist. Earlier range probe measured a 58,659,840-byte file; 2 MiB sample had 1,046 templates overlapping target dates. Current portal still says licence unspecified. | Promising large native schedule source, conditional on reuse rights. A sample count is not full-file/daily-flight coverage. |
| [Air Corsica](https://transport.data.gouv.fr/datasets/programme-des-vols-air-corsica) | Listed archives start 2024; summer/winter files; Open Licence 2.0. | Not a proven 2023 backfill. Current S26 SSIM retested; do not infer a winter26 renewal. |
| [Corsair](https://transport.data.gouv.fr/datasets/programme-corsair-au-30-juin-2025) | SSIM archive listings from 2024; licence unspecified. | No proven 2023 source or unrestricted reuse grant. |
| [Finist’Air](https://transport.data.gouv.fr/datasets/finistair-region-bretagne?locale=en) | Brest–Ouessant GTFS, ODbL plus specific conditions; archive list is bounded. | Current future file tested in main reference; no per-flight aircraft type. Historical2023 archive not established. |
| [Argentina ANAC movements](https://datos.transporte.gob.ar/dataset/21038a1a-c3c7-4494-b76a-3a2a8fbb83b5) | 2023 file metadata: 62,735,708 bytes, CC BY4.0. Earlier 32 KiB sample shows takeoff/landing events, airports, operator/model, but no flight number/registration or both endpoint times. | Good movement benchmark; pairing repeated events into a unique complete leg remains unresolved. |
| [South Korea Airportal/MOLIT](https://www.data.go.kr/data/15092485/fileData.do) | Free metadata advertises date/airport/airline queries and Excel exports; departure-board columns inspected. | No actual July–December2023 export or complete endpoint/type coverage verified. |
| [Finnair timetable](https://www.finnair.com/jp-ja/timetables) | Official route calendar and planned aircraft details; includes partner-operated AY marketing flights. | Consumer route interface, not a verified free bulk export/reuse feed. |

## Prospective and account-gated regional feeds

| Source / primary reference | Research finding | Limitation |
| --- | --- | --- |
| [Lufthansa OpenAPI plan](https://developer.lufthansa.com/product), [schedule endpoint](https://developer.lufthansa.com/docs/read/api_details/operations/Flight_Schedules) | Free registered plan; 6 calls/second, 1,000/hour; documented operations schedules to360 days. | Carrier/pair/date scope, account/terms; unrestricted full schedule listing not established. No authenticated payload tested. Status docs give conflicting past limits; no deep historical claim. |
| [Avinor](https://partner.avinor.no/tjenester/flydata/), [current public spec](https://xwiki.avinor.no/spaces/AASRV/pages/171475934/XmlFeed-%2BApplication%2Bdocumentation) | Free anonymous airport collections; documented36 hours past/336 hours future; cache and visibly attribute. | Events have one endpoint's time. Fresh7-hour and14-day collection probes are in main reference; not a 2023 archive. |
| [Swedavia](https://apideveloper.swedavia.se/faq), [guide](https://apideveloper.swedavia.se/content/Using_the_FlightInfo_API.pdf) | Earlier documentation: free key tier around10,000 calls/month, seven days back/90 ahead. | Quota-based, not unlimited; no authenticated coverage/retention probe. |
| [Finavia portal](https://apiportal.finavia.fi/), [website coverage](https://customerservice.finavia.fi/en/support/solutions/articles/79000119961-where-can-i-find-information-on-flight-schedules-) | Free API advertised; Helsinki passenger site separately describes two future months. | Website horizon is not proof of API entitlement. No registered probe. |
| [Schiphol](https://developer.schiphol.nl/) | Developer APIs and current JWT migration. | Free account does not establish unrestricted bulk/retention terms; do not use old onboarding as current. |
| [Hong Kong Airport](https://data.gov.hk/en-data/dataset/aahk-team1-flight-info) | Prior exact-date probe:2023-07-10→400; previous-day2026 control→200. | Historical feed is previous-day/limited, not unlimited history or proved future schedules; no type/opposite-event time. |
| [Lyon departures](https://www.data.gouv.fr/datasets/vols-au-depart-de-laeroport-lyon-saint-exupery-disponibilites-temps-reel) | Airport rolling operational events; earlier portal window day−3 to+1, labelled public domain. | No2023 archive or complete-leg/type guarantee. |
| [Taiwan TDX realtime](https://data.nat.gov.tw/en/datasets/157198) | Free registered/key-based real-time feed, Open Government Data License1.0. | Separate from our newly tested no-key CAA CSV. Quota/history/schema not verified. |
| [Incheon T1 operations](https://www.data.go.kr/data/15160568/openapi.do) | Free approval-based API; earlier docs day−3 to+6, subtype/registration, status/codeshares. | No2023 backfill. Metadata and description have permission/alteration ambiguity; no payload tested. |
| [Japan ODPT](https://developer.odpt.org/terms/data_basic_use_guideline.html) | Transport feeds and basic data-use guidelines. | Dynamic validity/display and provider-specific terms; no2023 flight export verified. |
| [AirLabs historical beta](https://airlabs.co/docs/historical) | Known-flight lookup and pagination; some scheduled fields labelled free. | No confirmed2023 depth, unlimited quota, complete enumeration or permanent-storage grant. |
| [Amadeus developer portal](https://developers.amadeus.com/) | October13 research observed self-service decommissioning notice; older free-tier tutorials remain online. | Old free self-service onboarding is superseded; no new unrestricted schedule feed demonstrated. |

The user-supplied handoff also examined Flight.info, FlightMapper, FR24's website
JSON, AirLabs Routes/Schedules, FlightAware, Aviationstack, AeroDataBox, Flightera,
ADSBDB and HexDB. Their current findings and primary links are retained in the
[main reference](../flight-data-sources.md#browser-readable-sources-and-commercial-apis).

## Research-only, aggregate or static alternatives

| Source / primary reference | Useful information | Why it cannot fill our complete itinerary table |
| --- | --- | --- |
| [EUROCONTROL ADRR](https://www.eurocontrol.int/dashboard/aviation-data-research), [terms](https://www.eurocontrol.int/sites/default/files/2025-04/eurocontol-aviation-data-repository-research-terms-of-use.pdf) | European operational records and types under vetted R&D access. | Public display/derived-product transfer restricted; not our unrestricted public-serving feed. |
| [FAA ASPM / SWIM](https://www.aspm.faa.gov/) | Operational/planned data where access is granted. | Individual-flight reports are non-public/elevated-access in the reviewed portal. |
| [OpenSky](https://opensky-network.org/about/faq), [published flight lists](https://zenodo.org/records/7323875) | Precomputed inferred-flight datasets reduce trace processing. | Inferred endpoints, version-specific licences, no new verified free2023 schedule export; no future schedules. |
| [UK CAA punctuality](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/flight-punctuality/statistics-guide/) | Airline/route punctuality benchmarks. | Aggregate counts, not individual dated flight times; reuse/resale notices apply. |
| [Australia BITRE](https://data.gov.au/data/en/dataset/domestic-airline-on-time-performance) | Monthly carrier/route punctuality. | Aggregates, not dated itineraries. |
| [BTS T-100](https://transtats.bts.gov/TableInfo.asp?QO_fu146_anzr=Nv4+Pn44vr45&gnoyr_VQ=GDM) | Monthly carrier/route/type totals. | Does not identify which aircraft type operated a particular OTP flight. |
| [Colombia Aerocivil](https://www.aerocivil.gov.co/atencion/estadisticas-de-las-actividades-aeronauticas/bases-de-datos) | Official operational/OD listings for2023 found. | Full dated-leg schema not obtained; reviewed open-data page mentioned noncommercial use. |
| [Singapore CAAS](https://data.gov.sg/datasets/d_744e62bfb1c524508bce0a64a2488243/view) | Open monthly traffic benchmarks. | Counts rather than individual flight legs. |
| [OpenFlights](https://openflights.org/data.php), [OurAirports](https://ourairports.com/data/), [FAA registry](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download) | Airport, old-route, airline, type and registration references. | Useful supplementary mappings, not dated future schedules or proof of a particular flight's actual type. |

Keep provenance/date validity and missing values. Combining weak route, aggregate
and registry data cannot manufacture an independently verified dated flight.
