# Manifiesto de subida del dataset

Instrucciones para subir datos a este repositorio sin cortes, sin pasarse de los
límites de GitHub y sin dejar lotes a medias. Están pensadas para una persona o
para un agente de IA (Claude, Copilot, Codex u otro) que trabaje en la germinadora
o en una Mac.

> **Este repositorio es público.** Todo lo que se sube lo puede ver cualquiera.

## Resumen en 4 comandos

Desde la raíz de `lumigrow-dataset`, después de **Guardar datos** en la app:

```bash
python3 scripts/subir_por_partes.py --auditar   # 1. ¿qué lotes están completos?
python3 scripts/subir_por_partes.py             # 2. plan de partes; no cambia nada
python3 scripts/subir_por_partes.py --subir     # 3. commit y push, parte por parte
python3 scripts/subir_por_partes.py --auditar   # 4. confirmar que quedó completo
```

Si el paso 3 se corta, **vuelve a correrlo**. Primero sube los commits que
quedaron locales y luego sigue con lo pendiente.

## Reglas que no se rompen

1. **Nunca `git push --force`**, ni `reset`, `rebase` o cambios a commits ya
   subidos. Si la rama local y la remota se separan, se detiene y decide una persona.
2. **Nunca borrar datos** para "hacer espacio" o "arreglar" una subida. El script
   no incluye borrados salvo con `--permitir-borrados`, y eso lo decide una persona.
3. **Nunca subir fotos originales** (`capturas/`). Viajan por USB o `rsync`.
   `.gitignore` ya las excluye; no lo cambies.
4. **Nunca subir secretos:** `.env`, `credentials/`, `rsa.json`, llaves, respaldos
   de firmware (`*.bin`) ni `wifi_local.h`. El script los bloquea.
5. **Nunca editar a mano** los CSV, `lote.db`, `checksums.sha256` ni los recortes.
   Si algo está mal, se vuelve a guardar el lote desde la app.
6. **El índice va al final.** `dataset_index.json` y las vistas por germinadora se
   suben después de los lotes, para que nunca nombren un lote que no llegó.
7. **Lote incompleto, lote detenido.** Si a un lote le faltan archivos de su
   `checksums.sha256`, no se sube hasta completarlo. La excepción es
   `--aceptar-incompletos`, y la decide una persona.

## Límites de GitHub y cómo los respeta el script

| Límite | Valor | Qué hace el script |
|---|---|---|
| Archivo | Rechaza 100 MB o más; avisa desde 50 MB | Bloquea desde 95 MB y avisa desde 50 MB |
| Un solo push | Hasta 2 GB | Partes de 400 MB como máximo (`--max-mb`) |
| Archivos por commit | Sin límite fijo, pero un commit enorme es lento y frágil | 5,000 por parte (`--max-archivos`) |
| Tamaño del repo | Ideal menos de 1 GB; muy recomendable menos de 5 GB | `--auditar` avisa en 3 GB y en 5 GB |
| Git LFS | En cuentas gratuitas, 1 GB de cuota | **No usar LFS** aquí |

Un lote de 10 días a 15 minutos con 24 plantas da unos 40,000 recortes de unos
7 KB, cerca de 300 MB. Eso cabe en una o dos partes por peso, pero son 8 o 9 partes
por número de archivos.

## Orden de cada subida

Por cada lote:

1. `crops/`: recortes, en partes de hasta 5,000 archivos o 400 MB.
2. Tablas del lote: CSV, `dataset.json`, `lote.db`, `environment/`,
   `checksums.sha256` y `LEEME.txt`.

Al final, `dataset_index.json`, las vistas por germinadora y cualquier archivo
de la raíz.

Cada parte es un commit con nombre claro, por ejemplo
`Datos LOT-20261028-651F-003: recortes 3/9`, y se sube en cuanto se hace. Si se
corta la conexión, se pierde a lo mucho una parte.

## En la germinadora (Raspberry Pi)

- Corre el script **con la app abierta pero sin estar guardando datos**.
- El push usa `pack.threads=2` y `pack.windowMemory=256m` para no agotar la
  memoria de la Pi, y corta una conexión que pasa 2 minutos casi sin avanzar.
- Si la red es inestable, baja el tamaño de las partes:
  `--max-mb 150 --max-archivos 2000`.
- Para una subida larga por SSH, usa `tmux` o `screen`, para que no se corte si se
  cierra la ventana.

## Qué hacer si algo falla

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| `rejected`, `fetch first`, `non-fast-forward` | Alguien subió algo antes | `git pull --ff-only` y volver a correr. Si no avanza "rápido", detenerse y avisar |
| `Authentication failed`, `403` | Token o permisos | Lo resuelve una persona. No pegar tokens en archivos ni en mensajes |
| `RPC failed`, `HTTP 400/500`, `early EOF` | La conexión se cortó a mitad de una parte | Volver a correr con `--max-mb 150 --max-archivos 2000` |
| `BLOQUEO: … 100 MB` | Un archivo no cabe en GitHub | No subirlo. Avisar: hay que dividirlo o guardarlo en otro almacenamiento |
| `BLOQUEO: … JSON dañado` | Un JSON quedó cortado | Volver a guardar el lote desde la app |
| `BLOQUEO: … no coinciden con checksums.sha256` | Un archivo cambió o se dañó | Volver a guardar el lote desde la app |
| `INCOMPLETO` | Faltan archivos del lote en esta copia | Volver a guardar el lote en la germinadora que lo tiene |
| `Hay cambios ya preparados` | Alguien hizo `git add` a mano | Revisarlos y `git restore --staged .` |

## Para agentes de IA

**Sin preguntar** puedes:

- correr `--auditar` y el plan sin `--subir`;
- correr `--subir` cuando la persona te pidió subir datos;
- reintentar `--subir` después de un corte de red;
- bajar `--max-mb` y `--max-archivos`.

**Pregunta antes de:**

- usar `--permitir-borrados` o `--aceptar-incompletos`;
- tocar `.gitignore`, el historial, otras ramas o la configuración de Git;
- subir algo que el script marca con `BLOQUEO`. No lo intentes por fuera del script.

**Al terminar,** reporta:

- cuántas partes subiste;
- la salida final de `--auditar`;
- cualquier lote `INCOMPLETO` o `FALTA` y su causa probable.

Nunca escribas en un archivo ni en un mensaje tokens, contraseñas o el contenido
de `.env`.

## Cuándo dejar de usar Git para los recortes

Cuando `.git` pase de unos 5 GB, o un clon tarde demasiado, los recortes deben ir
a otro almacenamiento, por ejemplo un bucket o Hugging Face Datasets. En Git se
quedarían las tablas, el índice y las huellas. El plan está en
`germinadora-central-module/docs/local-dataset-export-and-cloud-roadmap.md`.
Hasta entonces, este manifiesto es el camino.

## Estado al 26 de noviembre de 2026

`--auditar` muestra:

| Estado | Lotes |
|---|---|
| ANTIGUO: completos, de antes de `checksums.sha256` | 23 |
| INCOMPLETO | `LOT-20261028-651F-003`, berenjena Lana |
| FALTA | `LOT-20261107-651F-001` |

- **`LOT-20261028-651F-003`:** la subida del 24 de noviembre se cortó después de
  2 partes. Llegaron 9,988 de 41,712 recortes y `crop_observations.csv`. Faltan
  `lote.db`, `plants.csv`, `dataset.json`, `meshes.json`, `environment/`, la
  bitácora y los eventos.
- **`LOT-20261107-651F-001`:** el índice lo nombra, pero su carpeta nunca llegó.

**Para completarlos:** en la germinadora "Laboratorio 1", guardar los dos lotes
desde la app y correr el resumen de 4 comandos.
