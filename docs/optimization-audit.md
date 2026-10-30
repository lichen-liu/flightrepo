# Flight explorer optimization audit

Completed October 30, 2026. Scope: browser search and connection editing,
responsive navigation, observed-time presentation, Earth and timeline rendering,
backend response construction, and installed-package behavior.

## Findings addressed

| Finding | Result |
| --- | --- |
| Safari date separators caused card rendering to throw | Date, clock and offset now use cached `Intl.formatToParts` output. |
| A replacement search could overlap connection work from the previous query | Replacement searches cancel the editor and disable edit/undo actions until completion, including failure recovery. |
| Observed bounds appeared as exact clocks and durations in result cards | Cards show approximate clocks and elapsed ranges; layovers and chart labels distinguish minimum waits from duration upper bounds. |
| Phone airport suggestions covered the next airport input | Suggestions occupy space within the field, and option names include explicit accessible separators. |
| Date availability enabled an empty Journey tab | The tab remains disabled until a selected route exists; Earth remains available. |
| Antipodal airports and zero-duration flights could produce invalid map coordinates | Earth fitting and cursor interpolation retain finite coordinates. |
| Timeline scrubbing rebuilt static flight bars and tick labels | Only the cursor updates until width or flight timestamps change. |
| Local/public response construction repeatedly converted shared flight records | Validated models are retained and shared record conversions are reused within each response. |

Invalid custom preferences also close the popover so their error remains visible.
The configurable 7-day connection default and interactive Earth sphere remain in
the primary workflow.

## Measurements

The timeline regression applies 200 cursor updates. Static SVG construction fell
from 201 builds to one; resize, edited timestamps and cleared containers still
rebuild correctly.

A paired 61-round local backend benchmark used 100 itineraries containing 200 leg
occurrences and 101 distinct hydrated records. Combined response construction
fell from 5.454 ms to 2.380 ms median (about 56% less time); p95 fell from 5.957 ms
to 2.749 ms. Public JSON remained identical. SQLite identity resolution is
included; route calculation and HTTP transport are outside this measurement.

Raw local evidence is in `var/backend-response-benchmark.json`. Browser test logs
are `var/audit-chromium.log` and `var/audit-webkit.log`.

## Verification

- 1,433 Python tests passed.
- 46 frontend unit tests passed.
- All 65 browser checks passed in Chromium and all 65 passed in WebKit.
- Ruff lint/format checks and whitespace checks passed.
- Wheel build, installed-package probes and installed-ingestion probes passed.
- Manual browser checks confirmed timeline movement with retained bars and direct
  access to the next airport field at a 320 px phone width.

CI now runs the complete browser suite in both engines. Browser regressions check
flight identity, query constraints, paging, uncertainty, errors, keyboard use,
phone interactions, and same-origin requests. Tests wait for the rendered map
mode before comparing camera geometry and account for WebKit's native datalist
and clipboard behavior.

End-to-end search latency depends on route scope, available data and server load.
Wide date windows and deep result pages can still require substantial exact
routing work. See [routing performance](routing-performance.md) and
[web performance](web-performance.md) for the implementation and measurement
scope.
