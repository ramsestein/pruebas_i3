#!/usr/bin/env python3
"""Agrupa eventos por misma intubacion y se queda con el mas largo."""
import json
from collections import defaultdict

d = json.load(open('datasets/clinic_vitals/clinic_full_cases_index.json'))
events = d['events']

grupos = defaultdict(list)
for e in events:
    key = (e['box'], e['start_time'])
    grupos[key].append(e)

final = []
for key, evs in grupos.items():
    mejor = max(evs, key=lambda x: x['duration_seconds'])
    final.append(mejor)

final.sort(key=lambda x: x['duration_seconds'], reverse=True)

print(f'Eventos originales: {len(events)}')
print(f'Eventos sin duplicados (mas largo por intubacion): {len(final)}')
print()

durs = [e['duration_seconds'] for e in final]
print(f'Duracion media: {sum(durs)/len(durs)/3600:.1f}h')
print(f'Duracion mediana: {sorted(durs)[len(durs)//2]/3600:.1f}h')
print(f'Min: {min(durs)/3600:.1f}h  Max: {max(durs)/3600:.1f}h')
print()

cortos = sum(1 for d in durs if d < 7200)
print(f'Eventos < 2h: {cortos} ({cortos/len(durs)*100:.1f}%)')
print(f'Eventos >= 2h: {len(durs)-cortos} ({(len(durs)-cortos)/len(durs)*100:.1f}%)')
print()

print('Top 10 mas largos:')
for e in final[:10]:
    print(f"  {e['box']:8s} {e['start_time'][:16]} -> {e['end_time'][:16]} {e['duration_str']:25s} {e['num_vital_files_merged']:3d} arch")
