# LumiGrow datasets

Biblioteca de datasets de las germinadoras LumiGrow. Cada germinadora y cada
lote tienen una carpeta estable. Un investigador puede copiar la carpeta de su
lote completa sin usar Git ni buscar archivos en otras ubicaciones.

## Estructura

```text
lumigrow-dataset/
  README.md
  dataset_index.json
  MIGRATION_REPORT.json
  devices/
    <deviceId>/
      observations.csv
      crop_observations.csv
      vertex_manifest.jsonl
      vertex_manifest.metadata.jsonl
      <lotId>/
        LEEME.txt
        dataset.json
        observations.csv
        crop_observations.csv
        vertex_manifest.jsonl
        vertex_manifest.metadata.jsonl
        crops/*.jpg
```

Los archivos ubicados directamente bajo `<deviceId>/` son la vista acumulada
que consumen los notebooks actuales. Los archivos dentro de `<lotId>/` contienen
solo ese lote y usan rutas relativas como `crops/imagen.jpg`, por lo que la
carpeta se puede mover o copiar a una memoria USB.

## Schema v3

Ver [`germinadora-central-module/ai-pipeline/docs/data-audit-and-export-spec.md`](../germinadora-central-module/ai-pipeline/docs/data-audit-and-export-spec.md)
para el contrato de columnas y la migracion de schema.

Invariantes:

- en `crop_observations.csv`, `(plant_key, capture_group)` es único;
- una reorganización nunca modifica ni elimina los JPG existentes;
- volver a guardar un lote actualiza su carpeta sin borrar los demás lotes;
- `dataset_index.json` enumera todos los lotes conocidos, incluso los recuperados
  del historial del repositorio.

## Para investigadores

La aplicación Germinadora Central guarda por defecto en esta biblioteca. Usa
**Abrir biblioteca** para ver todos los lotes. Para entregar uno, copia completa
la carpeta `devices/<germinadora>/<lote>/`.

## Actualización interna del prototipo

Git queda reservado para el equipo principal. Después de guardar desde la app:

```bash
git status                # ver que cambio
git diff dataset_index.json
git add devices dataset_index.json
git commit -m "dataset update YYYY-MM-DD"
git push
```

La migración reproducible de carpetas portátiles se ejecuta con:

```bash
python3 scripts/migrate_portable_lots.py --apply
```

El script consulta también revisiones anteriores de Git para evitar que un
archivo global reemplazado deje lotes históricos fuera de sus carpetas.

## Donde NO vive aqui

- Notebooks de entrenamiento → `germinadora-central-module/ai-pipeline/notebooks/`.
- Modelos entrenados (`.keras`, `.tflite`, baselines) → `germinadora-central-module/ai-pipeline/models/`.
- Planeacion del pipeline, arquitectura, roadmap → `germinadora-central-module/ai-pipeline/docs/`.
- Logica de inferencia para el dashboard → `germinadora-central-module/python/inference/` (futuro).
