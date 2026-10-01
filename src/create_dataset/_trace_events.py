#!/usr/bin/env python3
"""
Para cada archivo .vital que empieza con CO2+TV_EXP y termina sin ellos
(extubación intra-archivo), busca hacia atrás en el tiempo dentro del
mismo box para encontrar cuándo comenzó la ventilación (intubación).
"""
import gzip, os, re
from collections import defaultdict
from datetime import datetime

FILENAME_PATTERN = re.compile(r'([a-z0-9]+)_(\d{6})_(\d{6})\.vital', re.IGNORECASE)

# 1. Cargar todos los archivos por box
box_files = defaultdict(list)

for root, _, files in os.walk('datasets/clinic_vitals'):
    for f in files:
        if not f.lower().endswith('.vital'):
            continue
        m = FILENAME_PATTERN.match(f)
        if not m:
            continue
        dt = datetime.strptime(m.group(2)+m.group(3), '%y%m%d%H%M%S')
        rel = os.path.relpath(root, 'datasets/clinic_vitals')
        box = rel.split(os.sep)[0]
        path = os.path.join(root, f)
        try:
            with gzip.open(path, 'rt', errors='replace') as gz:
                text = gz.read(50000)
            has_co2 = 'CO2' in text
            has_tv_exp = 'TV_EXP' in text
        except:
            has_co2 = has_tv_exp = False
        box_files[box].append((dt, path, has_co2, has_tv_exp))

for box in box_files:
    box_files[box].sort(key=lambda x: x[0])

# 2. Detectar extubaciones y trazar intubaciones hacia atrás
total_eventos = 0
duraciones = []

for box, files in sorted(box_files.items()):
    i = 0
    while i < len(files):
        dt, path, has_co2, has_tv_exp = files[i]
        is_vent = has_co2 and has_tv_exp

        if not is_vent:
            i += 1
            continue

        # Este archivo empieza con ventilación. Veamos si termina sin ella.
        try:
            with gzip.open(path, 'rt', errors='replace') as gz:
                text = gz.read(50000)
            mid = len(text) // 2
            co2_fin = 'CO2' in text[mid:]
            tv_exp_fin = 'TV_EXP' in text[mid:]
            vent_fin = co2_fin and tv_exp_fin
        except:
            vent_fin = True

        if vent_fin:
            i += 1
            continue

        # EXTUBA AQUÍ
        ext_time = dt

        # Ir hacia atrás para encontrar la intubación
        j = i - 1
        intub_time = None
        while j >= 0:
            prev_dt, prev_path, prev_co2, prev_tv_exp = files[j]
            prev_vent = prev_co2 and prev_tv_exp
            if not prev_vent:
                intub_time = files[j+1][0]
                break
            j -= 1

        if intub_time is None:
            intub_time = files[0][0]

        duracion = (ext_time - intub_time).total_seconds()
        duraciones.append(duracion)
        total_eventos += 1

        if total_eventos <= 5:
            print(
                f"  {box:8s} intub: {intub_time.strftime('%y%m%d_%H%M%S')} "
                f"-> ext: {ext_time.strftime('%y%m%d_%H%M%S')} "
                f"({duracion/3600:.1f}h) {os.path.basename(path)}"
            )

        i += 1

print(f"\nTotal eventos: {total_eventos}")
if duraciones:
    duraciones.sort()
    print(f"Duracion media: {sum(duraciones)/len(duraciones)/3600:.1f}h")
    print(f"Duracion mediana: {duraciones[len(duraciones)//2]/3600:.1f}h")
    print(f"Min: {duraciones[0]/3600:.1f}h  Max: {duraciones[-1]/3600:.1f}h")
    # Cuantos duran menos de 2h?
    cortos = sum(1 for d in duraciones if d < 7200)
    print(f"Eventos < 2h: {cortos} ({cortos/len(duraciones)*100:.1f}%)")
    print(f"Eventos >= 2h: {len(duraciones)-cortos} ({(len(duraciones)-cortos)/len(duraciones)*100:.1f}%)")
