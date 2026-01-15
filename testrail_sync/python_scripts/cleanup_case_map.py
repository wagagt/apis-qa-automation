import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'case_map.csv'
BAK = ROOT / 'case_map.csv.bak'
CLEAN = ROOT / 'case_map.clean.csv'

if not SRC.exists():
    print('case_map.csv not found at', SRC)
    raise SystemExit(1)

BAK.write_bytes(SRC.read_bytes())
rows = list(csv.DictReader(open(SRC, newline='')))
if not rows:
    print('case_map.csv empty')
    raise SystemExit(0)

by = {}
for r in rows:
    ts = r.get('timestamp','')
    key = r['title']
    if key not in by or ts > by[key].get('timestamp',''):
        by[key] = r

kept = list(by.values())
kept_ids = set((r['automation_id'], r['testrail_case_id']) for r in kept)
removed = [r for r in rows if (r['automation_id'], r['testrail_case_id']) not in kept_ids]

with open(CLEAN, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(sorted(kept, key=lambda x: x['title']))

print(f'Wrote {CLEAN} with {len(kept)} entries, removed {len(removed)} duplicate mappings')
if removed:
    print('\nCandidates to consider deleting in TestRail (automation_id, testrail_case_id, title):')
    for r in removed:
        print(r['automation_id'], r['testrail_case_id'], r['title'])
else:
    print('\nNo duplicate mappings found.')
