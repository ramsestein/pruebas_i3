import os
import random
from pathlib import Path
import vitaldb

def check_vital_dir(dir_path):
    print(f"\n==================================================")
    print(f"Revisando directorio: {dir_path}")
    print(f"==================================================")
    
    vital_files = list(Path(dir_path).rglob("*.vital"))
    if not vital_files:
        print("No se encontraron archivos .vital en este directorio.")
        return
        
    print(f"Total de archivos .vital encontrados: {len(vital_files)}")
    
    # Seleccionamos una muestra aleatoria de 5 archivos para no tardar una eternidad
    sample_files = random.sample(vital_files, min(5, len(vital_files)))
    print(f"Comprobando {len(sample_files)} archivos aleatorios como muestra...")
    
    all_enriched = True
    any_enriched = False
    
    for v_file in sample_files:
        try:
            # Leemos solo los nombres de los tracks del archivo (es rápido)
            track_names = vitaldb.vital_trks(str(v_file))
            
            # Verificamos si hay algún track que contenga 'Derived/'
            has_derived = any("Derived/" in t for t in track_names)
            
            if has_derived:
                print(f"  [ENRIQUECIDO] {v_file.name}")
                any_enriched = True
            else:
                print(f"  [NO ENRIQUECIDO] {v_file.name}")
                all_enriched = False
                
        except Exception as e:
            print(f"  [ERROR] No se pudo leer {v_file.name}: {e}")
            all_enriched = False
            
    print("\nRESUMEN DE ESTA CARPETA:")
    if all_enriched:
        print(">>> TODO indica que la carpeta está 100% ENRIQUECIDA.")
    elif any_enriched:
        print(">>> RESULTADO MIXTO. Algunos archivos están enriquecidos y otros no.")
    else:
        print(">>> NO ESTÁ ENRIQUECIDA. Los archivos comprobados son los datos en crudo originales.")

def main():
    check_vital_dir("datasets/vitaldb_sicu/vitaldb_full_cases")
    check_vital_dir("datasets/clinic_vitals/clinic_full_cases")

if __name__ == "__main__":
    main()
