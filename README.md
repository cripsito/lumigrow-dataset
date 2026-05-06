# lumigrow-dataset

Repositorio del dataset crudo de la germinadora. **Solo dataset y analisis
exclusivo del dato.** El pipeline de modelos, planeacion y bundle de
inferencia viven en el repo `germinadora-central-module/ai-pipeline/`.

## Layout

```
devices/<deviceId>/
  observations.csv               # telemetria ambiental por captura
  crop_observations.csv          # 1 fila por (planta x captura), con etiquetas
  vertex_manifest.jsonl          # manifest para Vertex AI
  vertex_manifest.metadata.jsonl # idem con _meta extendido
  <lotId>/
    crops/*.jpg
    dataset.json
dataset_index.json               # indice global, fuente de verdad de devices
```

## Schema v3

Ver [`germinadora-central-module/ai-pipeline/docs/data-audit-and-export-spec.md`](../germinadora-central-module/ai-pipeline/docs/data-audit-and-export-spec.md)
para el contrato de columnas y la migracion de schema.

Invariante de `crop_observations.csv`: `(plant_key, capture_group)` es unico.

## Flujo de actualizacion

El export de la app de la germinadora reescribe estos archivos in-place
(idempotente). Para subir cambios:

```bash
git status                # ver que cambio
git diff dataset_index.json
git add devices dataset_index.json
git commit -m "dataset update YYYY-MM-DD"
git push
```

El layout `devices/<dev>/<lot>/crops/*.jpg` es **append-only**: nunca se
reescriben crops existentes.

## Donde NO vive aqui

- Notebooks de entrenamiento → `germinadora-central-module/ai-pipeline/notebooks/`.
- Modelos entrenados (`.keras`, `.tflite`, baselines) → `germinadora-central-module/ai-pipeline/models/`.
- Planeacion del pipeline, arquitectura, roadmap → `germinadora-central-module/ai-pipeline/docs/`.
- Logica de inferencia para el dashboard → `germinadora-central-module/python/inference/` (futuro).
