import os
import pandas as pd
from pathlib import Path

def main():
    base_dir = Path("datasets")
    
    print("Buscando archivos .parquet en el directorio 'datasets'...\n")
    parquet_files = list(base_dir.rglob("*.parquet"))
    
    if not parquet_files:
        print("No se encontraron archivos .parquet.")
        return
    
    for p_file in parquet_files:
        try:
            # Leemos solo el schema/columnas sin cargar todo en memoria si es posible
            # o leemos 1 sola fila para ser rápidos
            df = pd.read_parquet(p_file, engine='pyarrow')
            columnas = df.columns.tolist()
            
            status = "NO ENRIQUECIDO"
            enriched_cols = []
            
            # Comprobamos si hay alguna columna que empiece por 'Derived/' u otros indicios de enriquecimiento
            for col in columnas:
                if isinstance(col, str) and ("Derived/" in col or "enrich" in col.lower() or "d1" in col or "auc_cum" in col):
                    enriched_cols.append(col)
                    
            if enriched_cols:
                status = "ENRIQUECIDO"
            
            print(f"[{status}] Archivo: {p_file}")
            print(f"    - Columnas totales: {len(columnas)}")
            if enriched_cols:
                print(f"    - Ejemplo de columnas derivadas/enriquecidas: {enriched_cols[:5]}")
            print("-" * 50)
            
        except Exception as e:
            print(f"[ERROR] No se pudo leer {p_file}: {e}")
            print("-" * 50)

if __name__ == "__main__":
    main()
