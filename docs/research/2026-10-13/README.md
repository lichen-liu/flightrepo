# Flyji customer research

R09 · 13 October 2026 · desk research, opposing-role debate and an independent process audit.

This round expands the customer questions before choosing more features or a final layout. [84 questions](needs.md) cover 21 areas for travelers, enthusiasts and researchers. They remain hypotheses. The [evidence ledger](evidence.md) separates public findings, product patterns, standards and owner requirements; the [structured register](register.json) traces every question to a proposed test and the earlier N01–N26 inventory.

Read the [first exploration slice](first-slice.md), [recommendations and alternatives](recommendations.md), [actual debate](debate.md) and [26-task validation plan](validation.md). The [customer advocate](advocate.md) and [evidence skeptic](skeptic.md) independently researched and challenged the work; the [QA plan](qa-plan.md) defines what their agreement can establish. Version-specific review receipts and the final audit are under `reviews/` when completed. A plain agent attestation records a performed review; its hashes identify bytes, without claiming a cryptographic signature or human approval.

## What matters enough to investigate first

The owner clarified that exploring, selecting, analyzing and playing with each flight option is the central task. The first slice follows browse, select, compare, vary a constraint/leg, inspect consequences, undo and retain an insight. Alongside that loop, test local days, waiting, transfer assumptions, changed identities, missing evidence and access. Commercial and personal travel needs remain unanswered boundaries. These priorities follow owner intent and research risk; no Flyji customer popularity ranking has been measured.

All three audiences remain in scope. Known-trip comparison, open exploration and service analysis may need different starting interactions. The three visual directions—Airlight, Journey canvas and Orbit studio—remain candidates. This round agrees on comparisons and evidence requirements; it does not choose a final default.

## QA and continuation

Run the local audit helper after the manifest and receipts are present:

```sh
.venv/bin/python scripts/verify_research.py --require-reviews
```

It checks IDs, traces, explicit pending human gates and reviewed file identities. It does not determine customer truth, source accuracy or usability by itself; those require the documented reviews and observations. Editing a reviewed artifact requires fresh review. Historical R07/R08 source and QA archives remain unchanged.

Continue with small experiments chosen from the unresolved questions, observe relevant real tasks, repair errors and reopen the decisions. Final design signoff needs the owner's acceptance of the specific proposal/version; customer-value and usability claims need observed people. Public-readiness evidence remains a separate gate. No participants were contacted, product code changed, branch pushed or public site deployed in this research round.
