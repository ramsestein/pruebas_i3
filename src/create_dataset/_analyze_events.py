#!/usr/bin/env python3
import json

d = json.load(open('datasets/clinic_vitals/clinic_full_cases_index.json'))
total = len(d['events'])
con_edu = [e for e in d['events'] if e['has_emergence']]
sin_edu = [e for e in d['events'] if not e['has_emergence']]
con_ind = [e for e in d['events'] if e['has_induction']]
sin_ind = [e for e in d['events'] if not e['has_induction']]

print(f"Eventos totales: {total}")
print(f"Con educción detectada: {len(con_edu)} ({len(con_edu)/total*100:.0f}%)")
print(f"Sin educción (gap final): {len(sin_edu)} ({len(sin_edu)/total*100:.0f}%)")
print(f"Con inducción detectada: {len(con_ind)} ({len(con_ind)/total*100:.0f}%)")
print(f"Sin inducción (gap inicial): {len(sin_ind)} ({len(sin_ind)/total*100:.0f}%)")
print()

print("--- Sin educción (extubación no capturada) ---")
for e in sorted(sin_edu, key=lambda x: x['duration_seconds'], reverse=True)[:20]:
    print(f"  {e['event_id']:30s} {e['box']:8s} {e['duration_str']:25s} {e['num_vital_files_merged']:3d} arch")

print()
print("--- Con educción detectada ---")
for e in sorted(con_edu, key=lambda x: x['duration_seconds'], reverse=True)[:20]:
    print(f"  {e['event_id']:30s} {e['box']:8s} {e['duration_str']:25s} {e['num_vital_files_merged']:3d} arch")
