"""Build a derived index; preserve the source rule library byte-for-byte."""
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/review_rules.json'
TARGET = ROOT / 'data/review_rule_index.json'

def build():
    raw = SOURCE.read_bytes()
    rules = json.loads(raw.decode('utf-8-sig'))['rules']
    groups = defaultdict(list)
    for rule in rules:
        groups[rule['rule']].append(rule)
    principles, entries = [], []
    for text, members in sorted(groups.items(), key=lambda item: min(x['id'] for x in item[1])):
        pid = 'P-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        principles.append({'principle_id': pid, 'source_text': text,
                           'original_rule_ids': [r['id'] for r in members],
                           'cross_scope_pooling_allowed': False,
                           'note': '共同文本索引，不代表跨场景通用规律；原文中的旧玩法不恢复。'})
        for r in members:
            triggers = r.get('scope', {}).get('trigger_conditions')
            entries.append({'original_rule_id': r['id'], 'principle_id': pid,
                            'source_pointer': f"rules[id={r['id']}]",
                            'scope': r.get('scope'),
                            'trigger_conditions': triggers or None,
                            'exclusion_conditions': r.get('scope', {}).get('exclusion_conditions', r.get('exclusion_conditions')),
                            'condition_status': 'recorded_requires_match_check' if triggers else 'legacy_requires_evidence_review',
                            'reported_sample_count': r.get('sample_count'),
                            'evidence_match_count': None,
                            'applied_match_count': None,
                            'paired_settled_match_count': None,
                            'count_status': 'not_reconstructed_from_legacy',
                            'source_status': r['status'],
                            'application_log_count': len(r.get('applications', [])),
                            'note': 'null是未核实，不是0或通配；日志条数不是独立比赛样本数。'})
    return {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'source_file': 'data/review_rules.json', 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'source_rule_count': len(rules), 'principle_count': len(principles),
            'policy': {'merge': 'exact_text_only', 'original_ids_preserved': True,
                       'application': '核对原规则和本场条件；每场共同原则只计一次影响，保留全部引用ID。',
                       'statistics': '按同联赛及触发场景中的独立比赛核验，不合并多版本或跨联赛计数。'},
            'principles': principles, 'rule_entries': sorted(entries, key=lambda x: x['original_rule_id'])}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='read-only freshness and content check')
    args = parser.parse_args()
    document = build()
    if args.check:
        saved = json.loads(TARGET.read_text(encoding='utf-8-sig'))
        saved.pop('generated_at', None)
        document.pop('generated_at', None)
        if saved != document:
            raise SystemExit('FAIL: index stale or inconsistent; regenerate before using it')
        print('PASS: source hash, all IDs, scopes and derived content match')
    else:
        if TARGET.exists():
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup = ROOT / '项目规则' / ('索引备份_' + stamp)
            backup.mkdir()
            (backup / TARGET.name).write_bytes(TARGET.read_bytes())
        TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"Indexed {document['source_rule_count']} original rules into {document['principle_count']} text principles")

if __name__ == '__main__':
    main()
