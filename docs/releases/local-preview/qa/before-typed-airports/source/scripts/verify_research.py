"""Validate the R09 research register and byte-specific review receipts (stdlib only).

This checks record integrity, not customer truth, website accessibility or source accuracy.
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / 'docs/research/2026-10-13'
REQUIRED_ARTIFACTS = {
    'docs/research/2026-10-13/' + name for name in
    ['README.md', 'recommendations.md', 'first-slice.md', 'needs.md', 'evidence.md',
     'validation.md', 'register.json', 'debate.md', 'advocate.md', 'skeptic.md', 'qa-plan.md']
} | {'scripts/verify_research.py'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_path(name):
    path = (REPO / name).resolve()
    if not path.is_relative_to(REPO):
        raise ValueError(f'Outside repository: {name}')
    return path


def verify(register_path, manifest_path, require_reviews):
    record = json.loads(register_path.read_text())
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    collections = ['owner_inputs', 'sources', 'needs', 'tasks', 'proposals', 'families', 'gates']
    ids = {}
    for collection in collections:
        values = record[collection]
        ids[collection] = {item['id'] for item in values}
        check(len(ids[collection]) == len(values), f'Duplicate {collection} IDs')
    for collection, prefix, count in [('needs', 'U', 84), ('tasks', 'T', 26),
                                      ('proposals', 'Q', 10), ('owner_inputs', 'O', 8),
                                      ('gates', 'G', 6)]:
        check(ids[collection] == {f'{prefix}{i:02}' for i in range(1, count + 1)},
              f'Incomplete/invalid R09 {collection} ID set')
        if collection != 'gates':
            check(record['declared_counts'][collection] == count,
                  f'Incorrect declared {collection} count')
    previous = set()
    audiences = set()
    for need in record['needs']:
        previous.update(need['previous_needs'])
        audiences.update(need['audiences'])
        check(need['test'] in ids['tasks'], f"Unknown task: {need['id']}")
        check(set(need['evidence']) <= ids['sources'], f"Unknown evidence: {need['id']}")
        check(need['evidence_state'] == 'hypothesis' and need['customer_validated'] is False,
              f"Unsupported validated need: {need['id']}")
        check(all(need[key] for key in ['question', 'input', 'response', 'audiences', 'previous_needs']),
              f"Incomplete need: {need['id']}")
        check(set(need['audiences']) <= {'traveler', 'enthusiast', 'researcher'},
              f"Unknown audience: {need['id']}")
        check(need['evidence_relation'] == 'context_only_not_validation'
              and bool(need['provenance']) and bool(need['current_support']),
              f"Missing hypothesis/support boundary: {need['id']}")
    check(previous == {f'N{i:02}' for i in range(1, 27)}, 'Earlier N01–N26 trace is incomplete/invalid')
    check(audiences == {'traveler', 'enthusiast', 'researcher'}, 'Audience coverage is incomplete/invalid')
    check(ids['families'] == {f'F{i:02}' for i in range(1, 13)}, 'Design-family coverage is incomplete')
    for proposal in record['proposals']:
        for field, collection in [('needs', 'needs'), ('tests', 'tasks'), ('families', 'families')]:
            check(bool(proposal[field]) and set(proposal[field]) <= ids[collection],
                  f"Broken proposal {field}: {proposal['id']}")
        check(bool(proposal['counterargument']), f"Missing counterargument: {proposal['id']}")
        check(proposal['owner_signoff'] == 'pending' and proposal['customer_validation'] == 'pending',
              f"Unsupported human decision: {proposal['id']}")
    for family in record['families']:
        check(set(family['tests']) <= ids['tasks'], f"Broken family task: {family['id']}")
    for task in record['tasks']:
        check(task['status'] == 'planned_not_run', f"Unsupported performed session: {task['id']}")
        check(all(task[key] for key in ['title', 'fixture', 'method', 'truth_and_observation',
                                       'customer_prompt', 'qa_actions']),
              f"Incomplete task truth/method: {task['id']}")
    check(record['customer_sessions'] == [], 'This desk round has no customer sessions')
    check(record['final_design_approved'] is False, 'Unsupported final-design approval')
    for gate in record['gates']:
        if gate['id'] in {'G05', 'G06'}:
            check(gate['human_required'] is True, f"Human gate was disabled: {gate['id']}")
            check(gate['state'] in {'pending', 'pending_not_run'}, f"Unsupported human gate: {gate['id']}")
    for source in record['sources']:
        check(source['url'].startswith('https://') and source['claim'] and source['limits'] and source['locator'],
              f"Incomplete source provenance: {source['id']}")

    manifest = json.loads(manifest_path.read_text())
    check(REQUIRED_ARTIFACTS <= set(manifest['artifacts']), 'Required artifacts omitted from manifest')
    for field in ['artifacts', 'inputs']:
        check(bool(manifest[field]), f'Empty manifest {field}')
        for name, sha in manifest[field].items():
            path = checked_path(name)
            check(path.is_file() and digest(path) == sha, f'Stale/missing {field}: {name}')
    check(digest(register_path) == manifest['artifacts'].get('docs/research/2026-10-13/register.json'),
          'Checked register differs from manifested register')
    for name in ['needs.md', 'validation.md']:
        contents = (BUNDLE / name).read_text()
        import re
        pattern = r'^\| (U\d+) \|' if name == 'needs.md' else r'^### (T\d+) ·'
        found = re.findall(pattern, contents, re.MULTILINE)
        expected = ids['needs'] if name == 'needs.md' else ids['tasks']
        check(len(found) == len(expected) and set(found) == expected, f'Markdown/register IDs differ: {name}')
    manifest_sha = digest(manifest_path)
    reviewed = []
    if require_reviews:
        for role in ['customer_advocate', 'evidence_skeptic', 'qa_auditor']:
            path = BUNDLE / 'reviews' / f'{role}.json'
            if not path.is_file():
                errors.append(f'Missing review: {role}')
                continue
            receipt = json.loads(path.read_text())
            check(receipt['reviewer'] == f'/root/{role}', f'Wrong reviewer: {role}')
            check(receipt['manifest_sha256'] == manifest_sha, f'Stale manifest review: {role}')
            check(receipt['outcome'] == 'accepted_research_plan', f'Unaccepted review: {role}')
            check(receipt['scope'] == 'desk_research_and_comparative_plan', f'Wrong review scope: {role}')
            check(receipt['human_signoff'] is False, f'Unsupported human signature: {role}')
            check(bool(receipt['timestamp_utc']) and bool(receipt['limitations']), f'Incomplete attestation: {role}')
            check(bool(receipt['method']) and bool(receipt['findings_disposition']), f'Missing review method/disposition: {role}')
            stamp = datetime.fromisoformat(receipt['timestamp_utc'].replace('Z', '+00:00'))
            check(stamp.utcoffset() is not None, f'Review timestamp lacks timezone: {role}')
            reviewed.append(role)
    return dict(ok=not errors, errors=errors, campaign=record['campaign'],
                needs=len(record['needs']), categories=len({n['category'] for n in record['needs']}),
                tasks=len(record['tasks']), proposals=len(record['proposals']),
                previous_needs=len(previous), design_families=len(ids['families']),
                sources=len(record['sources']), reviewed_roles=reviewed, manifest_sha256=manifest_sha,
                customer_sessions=0, final_design_approved=False,
                limitation='Integrity checks only; source and design judgments require the actual reviewer records.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--register', type=Path, default=BUNDLE / 'register.json')
    parser.add_argument('--manifest', type=Path, default=BUNDLE / 'manifest.json')
    parser.add_argument('--require-reviews', action='store_true')
    args = parser.parse_args()
    try:
        result = verify(args.register, args.manifest, args.require_reviews)
    except (KeyError, TypeError, ValueError, OSError) as error:
        result = dict(ok=False, errors=[str(error)])
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
