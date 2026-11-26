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
      capturas/                      # desde 3.1.82: fotos completas, una sola vez
        <AAAA-MM-DD>/
          checksums.sha256
          ambiente/*.jpg
          flash/*.jpg
      <lotId>/
        LEEME.txt
        dataset.json
        captures.csv                 # ruta de cada foto: ../capturas/<día>/<luz>/<archivo>
        meshes.json                  # coordenadas de las mayas: de aquí salen los recortes
        lote.db
        observations.csv
        crop_observations.csv
        vertex_manifest.jsonl
        vertex_manifest.metadata.jsonl
        crops/*.jpg                  # solo en lotes de antes de 3.1.82
```

Los archivos ubicados directamente bajo `<deviceId>/` son la vista acumulada
que consumen los notebooks actuales. Los archivos dentro de `<lotId>/` contienen
solo ese lote y usan rutas relativas como `crops/imagen.jpg`, por lo que la
carpeta se puede mover o copiar a una memoria USB.

### Lotes guardados como fotos (app 3.1.82 en adelante)

Los lotes nuevos no guardan recortes: guardan las **fotos completas**, una sola
vez por germinadora aunque varias charolas o lotes salgan en la misma foto, y
las coordenadas de sus mayas. Así se conserva todo lo que vio la cámara, también
lo que queda fuera de las celdas, y se sube menos. En `dataset.json`,
`crops_in_folder` vale `false`.

Para tener los recortes de uno de esos lotes:

```bash
pip install opencv-python-headless numpy
python3 scripts/recortar_desde_fotos.py --lote devices/<germinadora>/<lote>
```

Los recortes salen en `<lote>/crops/`, idénticos byte a byte a los que hace la
germinadora, y a **tamaño real** (cada celda con los píxeles que tiene en la
foto). Otras opciones: `--tamano 384` para el formato de antes, `--todos` para
todos los lotes con fotos, `--salida <carpeta>` y `--verificar` para comparar con
recortes existentes. El entrenamiento (`train_m1.py`) lo corre solo cuando le
faltan recortes. `subir_por_partes.py` no sube esos recortes rehechos.

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
la carpeta `devices/<germinadora>/<lote>/`. Si el lote se guardó como fotos,
copia también `devices/<germinadora>/capturas/` (o solo los días del lote), que
es donde están sus fotos.

## Actualización interna del prototipo

Git queda reservado para el equipo principal. Después de guardar desde la app,
sube **por partes** siguiendo [`MANIFIESTO-SUBIDA.md`](MANIFIESTO-SUBIDA.md):

```bash
python3 scripts/subir_por_partes.py --auditar   # estado de cada lote
python3 scripts/subir_por_partes.py             # plan, sin cambiar nada
python3 scripts/subir_por_partes.py --subir     # commit y push parte por parte
```

No uses `git add devices` de una sola vez: con lotes grandes el push se corta
(así quedó a medias la subida del 24 de noviembre de 2026).

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
