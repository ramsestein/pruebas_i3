import os
import shutil
import subprocess
from pathlib import Path

def main():
    print("==================================================")
    print("ENRIQUECIMIENTO VITALDB SICU")
    print("==================================================")

    base_dir = Path("datasets/vitaldb_sicu/vitaldb_full_cases")
    enriched_dir = Path("datasets/vitaldb_sicu/vitaldb_full_cases_enriched")

    # Si ya existía una carpeta enriched de un intento fallido, la borramos
    if enriched_dir.exists():
        shutil.rmtree(enriched_dir, ignore_errors=True)

    # 1. Ejecutar script de VitalDB
    script_path = r"src\create_dataset\enrich_vital_vitaldb.py"
    print(f"[{script_path}] Iniciando procesamiento...")
    
    python_exe = r"venv\Scripts\python.exe"
    # Le pasamos overwrite por si acaso
    result = subprocess.run([python_exe, script_path, "--overwrite"], capture_output=False)
    
    if result.returncode != 0:
        print(f"[{script_path}] ERROR durante la ejecución.")
        return

    print("Enriquecimiento completado. Procediendo a reemplazar la carpeta base...")

    # 2. Reemplazo
    if base_dir.exists() and enriched_dir.exists():
        print(f"  - Eliminando carpeta original: {base_dir}")
        shutil.rmtree(base_dir)
        print(f"  - Renombrando {enriched_dir} -> {base_dir}")
        enriched_dir.rename(base_dir)
        print("\n¡ÉXITO! Ahora vitaldb_full_cases contiene únicamente los casos enriquecidos.")
    else:
        print("  - Hubo un problema, no se generó la carpeta enriquecida.")

if __name__ == "__main__":
    main()
