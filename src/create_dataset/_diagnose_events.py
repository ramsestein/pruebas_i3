#!/usr/bin/env python3
import json

d = json.load(open('datasets/clinic_vitals/clinic_full_cases_index.json'))
sorted_events = sorted(d['events'], key=lambda e: e['duration_seconds'])

print('=== 5 MAS CORTOS ===')
for e in sorted_events[:5]:
    print(f"{e['event_id']:30s} {e['box']:8s} {e['duration_str']:25s} {e['num_vital_files_merged']:3d} archivos | {e['file']}")

print()
print('=== 5 MAS LARGOS ===')
for e in sorted_events[-5:]:
    print(f"{e['event_id']:30s} {e['box']:8s} {e['duration_str']:25s} {e['num_vital_files_merged']:3d} archivos | {e['file']}")

print()
print('=== DISTRIBUCION POR DURACION ===')
bins = [(0, 600), (600, 3600), (3600, 7200), (7200, 14400), (14400, 43200), (43200, 86400), (86400, 259200), (259200, 864000), (864000, 99999999)]
labels = ['<10m', '10m-1h', '1-2h', '2-4h', '4-12h', '12-24h', '1-3d', '3-10d', '>10d']
for (lo, hi), label in zip(bins, labels):
    cnt = sum(1 for e in d['events'] if lo <= e['duration_seconds'] < hi)
    print(f"  {label:10s}: {cnt} eventos")
