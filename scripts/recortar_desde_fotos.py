#!/usr/bin/env python3
"""Rehace los recortes de cada planta a partir de las fotos completas y la maya.

Desde la app 3.1.82 (D-83), los lotes nuevos se guardan como fotos completas
más sus mayas, sin la carpeta crops/: así se suben 24 veces menos archivos y
se conserva todo lo que vio la cámara. Este script vuelve a cortar cada
recorte con la misma geometría que usa la germinadora
(germinadora-central-module/python/workers/crop_worker.py), con el mismo
nombre de archivo que nombra crop_observations.csv. Así el entrenamiento y
los cuadernos funcionan igual que con los recortes de antes.

Dónde sirve:
  - Entrenar o analizar un lote nuevo: crea <lote>/crops/ (train_m1.py lo
    llama solo si le faltan recortes).
  - Probar otro tamaño o margen de recorte sin volver a la germinadora:
    --tamano real o --salida a otra carpeta.
  - Comprobar que los recortes de un lote antiguo salen igual de sus fotos:
    --verificar (no escribe nada).
  La app no lo necesita: al importar un lote guardado como fotos, ella misma
  recorta con su maya.

Uso (desde la raíz de lumigrow-dataset):
  python3 scripts/recortar_desde_fotos.py --lote devices/<germinadora>/<LOT-...>
  python3 scripts/recortar_desde_fotos.py --todos              # lotes que traen fotos
  python3 scripts/recortar_desde_fotos.py --lote ... --verificar
  python3 scripts/recortar_desde_fotos.py --lote ... --tamano real --salida /tmp/recortes

Necesita opencv-python-headless y numpy. Lee lote.db (o, si no está,
captures.csv, meshes.json y crop_observations.csv) y no cambia ningún archivo
del lote salvo crops/ (o la carpeta de --salida). Un recorte que ya existe no
se vuelve a escribir, salvo con --sobrescribir.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

FIXED_SIZE = 384      # recortes de lotes 'square' y valor por defecto de la app
FIXED_JPEG_QUALITY = 88   # recortes 384 o de proporción fija, como crop_worker.py
REAL_JPEG_QUALITY = 95    # recortes a tamaño real, como crop_worker.py (D-83)


# ── Geometría: copia exacta de crop_worker.py (si cambia allá, cambia aquí) ──

def bilinear(corners, u, v):
    tl, tr, br, bl = corners
    top_x = tl[0] + (tr[0] - tl[0]) * u
    top_y = tl[1] + (tr[1] - tl[1]) * u
    bot_x = bl[0] + (br[0] - bl[0]) * u
    bot_y = bl[1] + (br[1] - bl[1]) * u
    return (top_x + (bot_x - top_x) * v, top_y + (bot_y - top_y) * v)


def cell_quad(corners_norm, w, h, row, col, total_rows, total_cols, inset):
    corners_px = [(c['x'] * w, c['y'] * h) for c in corners_norm]
    u0, u1 = col / total_cols, (col + 1) / total_cols
    v0, v1 = row / total_rows, (row + 1) / total_rows
    pad_u = inset / total_cols
    pad_v = inset / total_rows
    return [
        bilinear(corners_px, u0 + pad_u, v0 + pad_v),
        bilinear(corners_px, u1 - pad_u, v0 + pad_v),
        bilinear(corners_px, u1 - pad_u, v1 - pad_v),
        bilinear(corners_px, u0 + pad_u, v1 - pad_v),
    ]


def real_size(quad):
    tl, tr, br, bl = quad
    dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    width = (dist(tl, tr) + dist(bl, br)) / 2
    height = (dist(tl, bl) + dist(tr, br)) / 2
    return max(8, int(round(width))), max(8, int(round(height)))


def cut(cv2, np, img, mesh, row, col, size):
    """size = (ancho, alto) fijo, o None para el tamaño real de la celda."""
    h, w = img.shape[:2]
    quad = cell_quad(mesh['corners'], w, h, row, col, mesh['rows'], mesh['cols'], mesh['inset'])
    cw, ch = size or real_size(quad)
    target = np.array([[0, 0], [cw - 1, 0], [cw - 1, ch - 1], [0, ch - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.array(quad, dtype=np.float32), target)
    return cv2.warpPerspective(img, m, (cw, ch), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


# ── Lectura del lote ─────────────────────────────────────────────────────────

def corners_of(value):
    corners = json.loads(value) if isinstance(value, str) else value
    return [{'x': float(c['x']), 'y': float(c['y'])} for c in corners]


def read_lot(lot_dir):
    """{mode, aspect, meshes{id: mesh}, images{id: ruta}, crops[{...}]}."""
    db_file = os.path.join(lot_dir, 'lote.db')
    if os.path.exists(db_file):
        db = sqlite3.connect(f'file:{db_file}?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            lot = db.execute('SELECT * FROM lots LIMIT 1').fetchone()
            meshes = {r['id']: {'corners': corners_of(r['corners_json']), 'rows': r['rows'], 'cols': r['cols'],
                                'inset': r['cell_inset'] if r['cell_inset'] is not None else 0.10}
                      for r in db.execute('SELECT * FROM lot_meshes')}
            images = {r['id']: r['filepath'] for r in db.execute('SELECT id, filepath FROM images')}
            crops = [{'filename': r['filename'], 'image_id': r['source_image_id'], 'mesh_id': r['mesh_id'],
                      'row': r['row_index'], 'col': r['col_index']}
                     for r in db.execute('SELECT filename, source_image_id, mesh_id, row_index, col_index FROM plant_crops')]
            keys = lot.keys() if lot else []
            mode = (lot['crop_aspect_mode'] if 'crop_aspect_mode' in keys else None) or 'square'
            aspect = (lot['crop_aspect_w'], lot['crop_aspect_h']) if 'crop_aspect_w' in keys and lot['crop_aspect_w'] else None
            return {'mode': mode, 'aspect': aspect, 'meshes': meshes, 'images': images, 'crops': crops}
        finally:
            db.close()

    # Sin lote.db: captures.csv + meshes.json + crop_observations.csv.
    with open(os.path.join(lot_dir, 'dataset.json'), encoding='utf-8') as fh:
        meta = json.load(fh)
    with open(os.path.join(lot_dir, 'meshes.json'), encoding='utf-8') as fh:
        mesh_rows = json.load(fh)
    meshes = {m['id']: {'corners': corners_of(m.get('corners_json') or m.get('corners')), 'rows': m['rows'], 'cols': m['cols'],
                        'inset': m.get('cell_inset') if m.get('cell_inset') is not None else 0.10} for m in mesh_rows}
    with open(os.path.join(lot_dir, 'captures.csv'), encoding='utf-8-sig', newline='') as fh:
        images = {int(r['image_id']): r['original_path'] for r in csv.DictReader(fh)}
    crops, seen = [], set()
    with open(os.path.join(lot_dir, 'crop_observations.csv'), encoding='utf-8-sig', newline='') as fh:
        for r in csv.DictReader(fh):
            for light in ('ambient', 'flash'):
                rel, image_id = r.get(f'{light}_crop_path'), r.get(f'{light}_image_id')
                if not rel or not image_id or not r.get('mesh_id'):
                    continue
                name = os.path.basename(rel)
                if name in seen:
                    continue
                seen.add(name)
                crops.append({'filename': name, 'image_id': int(image_id), 'mesh_id': int(r['mesh_id']),
                              'row': int(r['row_index']), 'col': int(r['col_index'])})
    dims = meta.get('crop_dims') or {}
    aspect = (dims.get('w'), dims.get('h')) if dims.get('w') else None
    return {'mode': meta.get('crop_aspect_mode') or 'square', 'aspect': aspect, 'meshes': meshes, 'images': images, 'crops': crops}


def size_for(lot, tamano):
    """Tamaño de salida: None = real de cada celda; si no, (ancho, alto)."""
    if tamano == 'real' or (tamano == 'auto' and lot['mode'] == 'real'):
        return None
    if tamano == '384' or lot['mode'] == 'square':
        return (FIXED_SIZE, FIXED_SIZE)
    return tuple(lot['aspect']) if lot['aspect'] else (FIXED_SIZE, FIXED_SIZE)


def photo_path(lot_dir, rel):
    """Ruta de la foto: ../capturas/<día>/... (3.1.82+) o capturas/... dentro del lote (antes)."""
    if not rel:
        return None
    full = os.path.normpath(os.path.join(lot_dir, rel))
    device_dir = os.path.dirname(os.path.normpath(lot_dir))
    if not full.startswith(device_dir + os.sep):
        return None
    return full if os.path.exists(full) else None


# ── Trabajo ──────────────────────────────────────────────────────────────────

def process_lot(cv2, np, lot_dir, out_dir, tamano, overwrite, verify):
    lot = read_lot(lot_dir)
    size = size_for(lot, tamano)
    quality = REAL_JPEG_QUALITY if size is None else FIXED_JPEG_QUALITY
    by_image = {}
    for c in lot['crops']:
        by_image.setdefault(c['image_id'], []).append(c)
    stats = {'escritos': 0, 'ya_estaban': 0, 'sin_foto': 0, 'sin_maya': 0, 'comparados': 0, 'diferencia_media': 0.0, 'peor': 0.0}
    if not verify:
        os.makedirs(out_dir, exist_ok=True)
    for image_id, crops in sorted(by_image.items()):
        targets = [c for c in crops if verify or overwrite or not os.path.exists(os.path.join(out_dir, c['filename']))]
        stats['ya_estaban'] += len(crops) - len(targets)
        if not targets:
            continue
        src = photo_path(lot_dir, lot['images'].get(image_id))
        img = cv2.imread(src) if src else None
        if img is None:
            stats['sin_foto'] += len(targets)
            continue
        for c in targets:
            mesh = lot['meshes'].get(c['mesh_id'])
            if not mesh:
                stats['sin_maya'] += 1
                continue
            crop = cut(cv2, np, img, mesh, c['row'], c['col'], size)
            dest = os.path.join(out_dir, c['filename'])
            if verify:
                old = cv2.imread(dest)
                if old is None or old.shape != crop.shape:
                    continue
                # El recorte guardado pasó por JPEG; se compara contra el nuevo también en JPEG.
                ok, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
                new = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                diff = float(np.abs(new.astype(np.int16) - old.astype(np.int16)).mean())
                stats['comparados'] += 1
                stats['diferencia_media'] += diff
                stats['peor'] = max(stats['peor'], diff)
                continue
            temp = f'{dest}.tmp.jpg'
            if not cv2.imwrite(temp, crop, [cv2.IMWRITE_JPEG_QUALITY, quality]):
                raise RuntimeError(f'no se pudo escribir {dest}')
            os.replace(temp, dest)
            stats['escritos'] += 1
    if stats['comparados']:
        stats['diferencia_media'] = round(stats['diferencia_media'] / stats['comparados'], 3)
    stats['peor'] = round(stats['peor'], 3)
    return stats


def lots_with_photos(root):
    found = []
    devices = os.path.join(root, 'devices')
    for dev in sorted(os.listdir(devices)) if os.path.isdir(devices) else []:
        if not os.path.isdir(os.path.join(devices, dev, 'capturas')):
            continue
        for name in sorted(os.listdir(os.path.join(devices, dev))):
            d = os.path.join(devices, dev, name)
            if name.startswith('LOT-') and (os.path.exists(os.path.join(d, 'lote.db')) or os.path.exists(os.path.join(d, 'captures.csv'))):
                found.append(d)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lote', action='append', default=[], help='carpeta del lote (se puede repetir)')
    ap.add_argument('--todos', action='store_true', help='todos los lotes de germinadoras que traen capturas/')
    ap.add_argument('--tamano', choices=['auto', 'real', '384'], default='auto',
                    help='auto: como lo hizo la germinadora (por defecto); real: cada celda a su tamaño en la foto; 384: cuadrado fijo')
    ap.add_argument('--salida', help='carpeta de salida (por defecto <lote>/crops; con varios lotes, <salida>/<lote>)')
    ap.add_argument('--sobrescribir', action='store_true', help='volver a escribir recortes que ya existen')
    ap.add_argument('--verificar', action='store_true', help='comparar contra los recortes que ya están, sin escribir')
    a = ap.parse_args()

    try:
        import cv2
        import numpy as np
    except ImportError:
        raise SystemExit('Falta OpenCV: pip install opencv-python-headless numpy')

    lots = [os.path.normpath(d) for d in a.lote] + (lots_with_photos('.') if a.todos else [])
    if not lots:
        raise SystemExit('Indica --lote <carpeta> o --todos.')
    total_bad = 0
    for lot_dir in lots:
        if not os.path.isdir(lot_dir):
            print(f'{lot_dir}: no existe')
            total_bad += 1
            continue
        if a.salida:
            out = a.salida if len(lots) == 1 else os.path.join(a.salida, os.path.basename(lot_dir))
        else:
            out = os.path.join(lot_dir, 'crops')
        try:
            s = process_lot(cv2, np, lot_dir, out, a.tamano, a.sobrescribir, a.verificar)
        except (OSError, ValueError, KeyError, sqlite3.Error) as e:
            print(f'{lot_dir}: no se pudo leer ({e})')
            total_bad += 1
            continue
        if a.verificar:
            print(f'{lot_dir}: {s["comparados"]} recortes comparados, diferencia media {s["diferencia_media"]} '
                  f'(de 255), peor {s["peor"]}; sin foto: {s["sin_foto"]}')
        else:
            print(f'{lot_dir}: {s["escritos"]} recortes nuevos, {s["ya_estaban"]} ya estaban, '
                  f'{s["sin_foto"]} sin foto, {s["sin_maya"]} sin maya → {out}')
        if s['sin_maya']:
            total_bad += 1
    sys.exit(1 if total_bad else 0)


if __name__ == '__main__':
    main()
