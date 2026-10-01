"""Explorar señales disponibles en MIMIC4"""
import wfdb, os

mimic4 = 'datasets/mimic4'
boxes = sorted([d for d in os.listdir(mimic4) if d.startswith('p') and os.path.isdir(os.path.join(mimic4, d))])

all_signals = set()
count = 0
for box in boxes:
    box_path = os.path.join(mimic4, box)
    for sub in os.listdir(box_path):
        sub_path = os.path.join(box_path, sub)
        if not os.path.isdir(sub_path):
            continue
        # Buscar subcarpeta con archivos .hea (ej: 81739927/)
        for rec_dir in os.listdir(sub_path):
            rec_path = os.path.join(sub_path, rec_dir)
            if not os.path.isdir(rec_path):
                continue
            for f in os.listdir(rec_path):
                if f.endswith('.hea') and '_' not in f:
                    record = f.replace('.hea','')
                    try:
                        h = wfdb.rdheader(os.path.join(rec_path, record))
                        for seg in h.seg_name:
                            try:
                                sh = wfdb.rdheader(os.path.join(rec_path, seg))
                                all_signals.update(sh.sig_name)
                            except:
                                pass
                    except:
                        pass
                    count += 1
                    break
            break
        break
    if count >= 20:
        break

print(f'Checked {count} patients')
print(f'All unique signals: {sorted(all_signals)}')
