import os
import shutil
import subprocess
from pathlib import Path

def run_script(script_path):
    print(f"\n[{script_path}] Iniciando...")
    # Usamos el python del entorno virtual
    python_exe = r"venv\Scripts\python.exe"
    result = subprocess.run([python_exe, script_path], capture_output=False)
    if result.returncode != 0:
        print(f"[{script_path}] ERROR durante la ejecución.")
        return False
    print(f"[{script_path}] Finalizado con éxito.")
    return True

def replace_with_enriched(base_dir_str, enriched_dir_str):
    base_dir = Path(base_dir_str)
    enriched_dir = Path(enriched_dir_str)

    if not enriched_dir.exists():
        print(f"Error: No se encontró la carpeta enriquecida {enriched_dir}")
        return

    # Comprobamos que tenga archivos (asegurando que el script generó algo)
    enriched_files = list(enriched_dir.glob("*.vital"))
    if not enriched_files:
        print(f"Error: La carpeta {enriched_dir} está vacía. Abortando reemplazo.")
        return

    print(f"\nReemplazando {base_dir} por los casos enriquecidos...")
    # 1. Borrar la carpeta original
    if base_dir.exists():
        print(f"  - Eliminando carpeta original: {base_dir}")
        shutil.rmtree(base_dir)
    
    # 2. Renombrar la carpeta enriquecida para que tome el lugar de la original
    print(f"  - Renombrando {enriched_dir} -> {base_dir}")
    enriched_dir.rename(base_dir)
    print("  - Reemplazo completado.")


def main():
    print("==================================================")
    print("ENRIQUECIMIENTO DE DATASETS (MIMIC-III & VitalDB)")
    print("==================================================")

    # 1. Ejecutar script de VitalDB
    # vitaldb lee de datasets/vitaldb_sicu/vitaldb_full_cases
    vitaldb_script = r"src\create_dataset\enrich_vital_vitaldb.py"
    if run_script(vitaldb_script):
        replace_with_enriched(
            "datasets/vitaldb_sicu/vitaldb_full_cases",
            "datasets/vitaldb_sicu/vitaldb_full_cases_enriched"
        )

    # 2. Ejecutar script de MIMIC
    # MIMIC lee de datasets/mimic3wdb/mimic_full_cases
    mimic_script = r"src\create_dataset\enrich_mimic_full_cases.py"
    if run_script(mimic_script):
        replace_with_enriched(
            "datasets/mimic3wdb/mimic_full_cases",
            "datasets/mimic3wdb/mimic_full_cases_enriched"
        )

    print("\n==================================================")
    print("¡PROCESO FINALIZADO!")
    print("Ahora todos los datasets base contienen únicamente los casos enriquecidos.")

if __name__ == "__main__":
    main()
