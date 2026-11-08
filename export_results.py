#!/usr/bin/env python3
"""Export Freedom CQA evaluations to the Almighty hackathon JSON schema.

Run: python export_results.py --dataset dev --verify-message-ids
Only pass --verify-message-ids after confirming mN means original transcript turn N,
including client, bot and event entries (1-based).
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CRITERIA = {1: ('contact', 10, 'bool', False), 2: ('discovery', 15, 'bool', True),
            3: ('identification', 20, 'bool', True), 4: ('consultation', 20, 'scale20', True),
            5: ('empathy', 15, 'bool', False), 6: ('dialogue', 15, 'scale10', False),
            7: ('closure', 5, 'bool', True)}
EXPECTED = {'dev': 65, 'prod': 10}
MODEL = 'nvidia/nemotron-3-ultra-550b-a55b:free'


def fail(message):
    raise ValueError(message)


def evidence_turns(item, cid):
    ids = item.get('evidence_message_ids', [])
    if not isinstance(ids, list):
        fail(f'{cid}: evidence_message_ids must be a list')
    turns = []
    for mid in ids:
        match = re.fullmatch(r'm([1-9][0-9]*)', str(mid))
        if not match:
            fail(f'{cid}: cannot map evidence ID {mid!r}; verify original transcript indices')
        turns.append(int(match.group(1)))
    return sorted(set(turns))


def convert_criterion(item, cid):
    number = item.get('id')
    if number not in CRITERIA:
        fail(f'{cid}: unknown criterion id {number!r}')
    code, weight, kind, nullable = CRITERIA[number]
    raw = item.get('result')
    if raw == 'N/A' or raw is None:
        if not nullable:
            fail(f'{cid}: {code} cannot be N/A')
        value = None
    elif kind == 'bool':
        if raw not in ('YES', 'NO', True, False):
            fail(f'{cid}: {code} expects YES/NO, got {raw!r}')
        value = raw in ('YES', True)
    else:
        valid = (0, 5, 10, 15, 20) if kind == 'scale20' else (0, 4, 7, 10)
        if type(raw) is not int or raw not in valid:
            fail(f'{cid}: {code} expects one of {valid}, got {raw!r}')
        value = raw
    explanation = item.get('explanation')
    if not isinstance(explanation, str) or not explanation.strip():
        fail(f'{cid}: {code} is missing rationale')
    return {'criterion_id': code, 'value': value,
            'rationale': explanation.strip(), 'evidence_turns': evidence_turns(item, cid)}


def convert(record):
    cid = str(record.get('conversation_id', '')).strip()
    if not cid:
        fail('Evaluation missing conversation_id')
    original = record.get('criteria')
    if not isinstance(original, list) or len(original) != 7:
        fail(f'{cid}: expected seven original scored criteria')
    by_id = {}
    for item in original:
        if not isinstance(item, dict) or item.get('id') in by_id:
            fail(f'{cid}: duplicate/invalid original criterion')
        by_id[item['id']] = item
    if set(by_id) != set(CRITERIA):
        fail(f'{cid}: original criteria must have IDs 1..7')
    criteria = [convert_criterion(by_id[i], cid) for i in range(1, 8)]
    gross = record.get('gross_errors')
    if not isinstance(gross, dict) or type(gross.get('detected')) is not bool:
        fail(f'{cid}: gross_errors.detected must be boolean')
    criteria.append({'criterion_id': 'critical', 'value': gross['detected'],
                     'rationale': gross.get('explanation') or ('Gross violation detected.' if gross['detected'] else 'No gross violations detected.'),
                     'evidence_turns': evidence_turns(gross, cid)})
    numerator = denominator = 0.0
    for item in criteria[:-1]:
        code = item['criterion_id']
        spec = next(spec for spec in CRITERIA.values() if spec[0] == code)
        _, weight, kind, _ = spec
        value = item['value']
        if value is None:
            continue
        denominator += weight
        numerator += weight * (float(value) / (20 if kind == 'scale20' else 10) if kind.startswith('scale') else int(value))
    if not denominator:
        fail(f'{cid}: no applicable criteria')
    score = round(100 * numerator / denominator + 1e-10, 2)
    old_score = record.get('total_score')
    if isinstance(old_score, (int, float)) and abs(old_score - score) > 0.011:
        fail(f'{cid}: original total_score={old_score}, recomputed={score}; investigate scoring before export')
    return {'conversation_id': cid, 'status': 'ok', 'criteria': criteria,
            'score': {'final_score': score, 'critical_flag': gross['detected']}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', choices=('dev', 'prod'), default='dev')
    parser.add_argument('--source', type=Path, default=ROOT / 'evaluations.jsonl')
    parser.add_argument('--verify-message-ids', action='store_true',
                        help='Confirm mN IDs equal original 1-based transcript turn indices')
    args = parser.parse_args()
    if not args.verify_message_ids:
        parser.error('First verify that m10 means original transcript turn 10 (including client/bot/events), then pass --verify-message-ids')
    if not args.source.is_file():
        parser.error(f'Cannot find {args.source}')
    records = []
    seen = set()
    with args.source.open(encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = convert(json.loads(line))
            except (ValueError, TypeError, KeyError) as e:
                parser.error(f'Line {line_no}: {e}')
            cid = record['conversation_id']
            if cid in seen:
                parser.error(f'Duplicate conversation_id: {cid}')
            seen.add(cid)
            records.append(record)
    if len(records) != EXPECTED[args.dataset]:
        parser.error(f'Expected {EXPECTED[args.dataset]} unique {args.dataset.upper()} evaluations, found {len(records)}. No file written.')
    models = set()
    with args.source.open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                models.add(json.loads(line).get('model'))
    if None in models or not models:
        parser.error('Model name missing in source evaluation(s)')
    payload = {'schema_version': '1.0', 'team_name': 'Almighty',
               'model': {'mode': 'external_api', 'name': ', '.join(sorted(models))},
               'results': records}
    destination = ROOT / 'results' / args.dataset / 'results.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(records)} evaluations to {destination}')
    print('IMPORTANT: Independently verify every conversation_id against the original API dataset before submission.')

if __name__ == '__main__':
    main()
