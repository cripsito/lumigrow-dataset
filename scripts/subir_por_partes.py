#!/usr/bin/env python3
"""Sube a GitHub los cambios del dataset en partes pequeñas y en orden seguro.

Lee MANIFIESTO-SUBIDA.md antes de usarlo. Solo usa la biblioteca estándar de
Python 3.8+ y Git 2.26+, así que corre igual en la germinadora (Raspberry) que
en una Mac.

  python3 scripts/subir_por_partes.py --auditar   estado de cada lote (no cambia nada)
  python3 scripts/subir_por_partes.py             plan de partes (no cambia nada)
  python3 scripts/subir_por_partes.py --subir     commit y push, parte por parte

Orden: primero las fotos de cada germinadora (devices/<id>/capturas/<día>/),
después cada lote, con sus recortes (crops/) si los tiene y luego sus datos (CSV,
JSON, lote.db), y al final el índice general. Así ni un lote nombra una foto que
no llegó ni el índice nombra un lote que no llegó completo.

Desde la app 3.1.82 (D-83), los lotes nuevos se guardan como fotos completas, una
sola vez por germinadora, más las coordenadas de sus mayas; los recortes se
rehacen con scripts/recortar_desde_fotos.py. Las copias de fotos dentro de cada
lote (devices/<id>/LOT-*/capturas/, de antes) siguen fuera de Git.

Nunca borra archivos, nunca reescribe la historia y nunca usa --force. Si una
subida falla, los commits quedan locales; al volver a correrlo, primero sube
esos y luego sigue con lo pendiente.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time

MB = 1024 * 1024
LIMITE_GITHUB = 100 * MB      # GitHub rechaza archivos de 100 MiB o más
BLOQUEO = 95 * MB             # margen para no rozar el límite
AVISO = 50 * MB               # GitHub avisa desde 50 MiB
PROHIBIDO = [
    (re.compile(r'^devices/[^/]+/LOT-[^/]+/capturas/'),
     'copia de foto dentro de un lote (antes de 3.1.82): las fotos van una sola vez en devices/<id>/capturas/'),
    (re.compile(r'(^|/)\.env($|\.)'), 'archivo de entorno'),
    (re.compile(r'(^|/)(credentials|secrets?)(/|\.|$)', re.I), 'credenciales'),
    (re.compile(r'(^|/)rsa\.json$|\.(pem|key|p12)$', re.I), 'llave privada'),
    (re.compile(r'\.bin$', re.I), 'respaldo de firmware: puede llevar contraseñas'),
    (re.compile(r'wifi_local\.h$', re.I), 'contraseñas Wi-Fi'),
]


def git(*args, check=True, capture=True, extra_config=()):
    cmd = ['git']
    for c in extra_config:
        cmd += ['-c', c]
    cmd += list(args)
    r = subprocess.run(cmd, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        raise SystemExit(f"\nFalló: {' '.join(cmd)}\n{(r.stderr or b'').decode(errors='replace').strip()}")
    return r


def out(*args):
    return git(*args).stdout.decode().strip()


def fmt_mb(n):
    return f'{n / MB:,.1f} MB'


def raiz():
    top = out('rev-parse', '--show-toplevel')
    if not os.path.exists(os.path.join(top, 'dataset_index.json')):
        raise SystemExit('Esto no parece lumigrow-dataset: falta dataset_index.json en la raíz.')
    os.chdir(top)
    return top


def pendientes():
    """[(estado, ruta)] de lo que cambió, sin lo ignorado por .gitignore."""
    raw = git('status', '--porcelain=v1', '-z', '-uall').stdout.decode('utf-8', 'surrogateescape')
    items, partes, i = [], raw.split('\0'), 0
    while i < len(partes):
        e = partes[i]
        i += 1
        if not e:
            continue
        estado, ruta = e[:2], e[3:]
        if estado[0] in 'RC':          # renombrado: la ruta vieja viene después
            i += 1
        items.append((estado, ruta))
    return items


def lote_de(ruta):
    p = ruta.split('/')
    if len(p) >= 4 and p[0] == 'devices' and p[2].startswith('LOT-'):
        return f'{p[1]}/{p[2]}'
    return None


def foto_de(ruta):
    """(germinadora, día) de una foto o huella en devices/<id>/capturas/<día>/…"""
    p = ruta.split('/')
    if len(p) >= 5 and p[0] == 'devices' and p[2] == 'capturas':
        return p[1], p[3]
    return None


def leer_checksums(carpeta):
    f = os.path.join(carpeta, 'checksums.sha256')
    if not os.path.exists(f):
        return None
    sums = {}
    with open(f, encoding='utf-8') as fh:
        for linea in fh:
            if '  ' not in linea:
                continue
            h, nombre = linea.rstrip('\n').split('  ', 1)
            nombre = nombre.lstrip('*')
            if not nombre.startswith('capturas/'):
                sums[nombre] = h
    return sums


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def revisar_lote(lote, rutas_pendientes, verificar_hash):
    """Compara la carpeta del lote con su checksums.sha256."""
    return revisar_carpeta(os.path.join('devices', lote), rutas_pendientes, verificar_hash)


def revisar_carpeta(carpeta, rutas_pendientes, verificar_hash):
    """Compara una carpeta (lote o día de fotos) con su checksums.sha256."""
    sums = leer_checksums(carpeta)
    if sums is None:
        return {'checksums': False, 'faltan': [], 'distintos': []}
    faltan = [n for n in sums if not os.path.exists(os.path.join(carpeta, n))]
    distintos = []
    if verificar_hash:
        for r in rutas_pendientes:
            n = r[len(carpeta) + 1:]
            if n in sums and os.path.exists(r) and sha256(r) != sums[n]:
                distintos.append(n)
    return {'checksums': True, 'faltan': faltan, 'distintos': distintos}


def es_lote_de_fotos(lote, _cache={}):
    """True si el lote se guardó como fotos (3.1.82+): sus recortes se rehacen, no se suben."""
    if lote not in _cache:
        try:
            with open(os.path.join('devices', lote, 'dataset.json'), encoding='utf-8') as fh:
                _cache[lote] = json.load(fh).get('crops_in_folder') is False
        except (OSError, ValueError):
            _cache[lote] = False
    return _cache[lote]


def planear(items, max_mb, max_archivos, permitir_borrados):
    problemas, avisos, omitidos = [], [], []
    por_lote, fotos, general, borrados = {}, {}, [], []
    for estado, ruta in items:
        if 'D' in estado:
            borrados.append(ruta)
            if not permitir_borrados:
                continue
        lote = lote_de(ruta)
        if lote and ruta.split('/')[3] == 'crops' and es_lote_de_fotos(lote):
            omitidos.append(ruta)   # rehechos con recortar_desde_fotos.py
            continue
        for patron, motivo in PROHIBIDO:
            if patron.search(ruta):
                problemas.append(f'{ruta}: {motivo}')
                break
        size = os.path.getsize(ruta) if os.path.isfile(ruta) else 0
        if ruta.endswith('.json') and os.path.isfile(ruta):
            try:
                with open(ruta, encoding='utf-8') as fh:
                    json.load(fh)
            except ValueError as e:
                problemas.append(f'{ruta}: JSON dañado ({e})')
        if size >= BLOQUEO:
            problemas.append(f'{ruta}: {fmt_mb(size)}; GitHub rechaza archivos de 100 MB o más')
        elif size >= AVISO:
            avisos.append(f'{ruta}: {fmt_mb(size)}; GitHub avisa desde 50 MB')
        dia = foto_de(ruta)
        if dia:
            fotos.setdefault(dia[0], {'fotos': [], 'huellas': []})
            clave = 'huellas' if ruta.endswith('/checksums.sha256') else 'fotos'
            fotos[dia[0]][clave].append((ruta, size))
        elif lote:
            por_lote.setdefault(lote, {'pesados': [], 'datos': []})
            clave = 'pesados' if ruta.split('/')[3] == 'crops' else 'datos'
            por_lote[lote][clave].append((ruta, size))
        else:
            general.append((ruta, size))

    partes = []

    def dias(rutas):
        d = sorted({foto_de(r)[1] for r in rutas})
        return d[0] if len(d) == 1 else f'{d[0]} a {d[-1]}'

    def trocear(lista, titulo, con_dias=False):
        lista = sorted(lista)
        bloques, actual, peso = [], [], 0
        for ruta, size in lista:
            if actual and (len(actual) >= max_archivos or peso + size > max_mb * MB):
                bloques.append((actual, peso))
                actual, peso = [], 0
            actual.append(ruta)
            peso += size
        if actual:
            bloques.append((actual, peso))
        for i, (rutas, peso) in enumerate(bloques, 1):
            sufijo = f' {i}/{len(bloques)}' if len(bloques) > 1 else ''
            if con_dias:
                sufijo += f' ({dias(rutas)})'
            partes.append({'titulo': f'{titulo}{sufijo}', 'rutas': rutas, 'peso': peso})

    # Las fotos van antes que los lotes: captures.csv de un lote las nombra.
    # Las huellas de cada día, después de sus fotos.
    for dev in sorted(fotos):
        trocear(fotos[dev]['fotos'], f'Fotos {dev}', con_dias=True)
        trocear(fotos[dev]['huellas'], f'Fotos {dev}: huellas por día', con_dias=True)
    for lote in sorted(por_lote):
        nombre = lote.split('/')[1]
        trocear(por_lote[lote]['pesados'], f'Datos {nombre}: recortes')
        trocear(por_lote[lote]['datos'], f'Datos {nombre}: tablas del lote')
    trocear(general, 'Índice y vistas generales del dataset')
    dias_tocados = sorted({foto_de(r) for d in fotos.values() for r, _ in d['fotos'] + d['huellas']})
    if omitidos:
        avisos.append(f'{len(omitidos)} recortes de lotes guardados como fotos no se suben: se rehacen con '
                      f'scripts/recortar_desde_fotos.py. Ejemplo: {omitidos[0]}')
    return partes, problemas, avisos, borrados, sorted(por_lote), dias_tocados


def estado_rama(remoto, rama):
    git('fetch', '--quiet', remoto, rama)
    atras = int(out('rev-list', '--count', f'HEAD..{remoto}/{rama}'))
    adelante = int(out('rev-list', '--count', f'{remoto}/{rama}..HEAD'))
    return atras, adelante


def push(remoto, rama, intentos=4):
    cfg = ('pack.threads=2', 'pack.windowMemory=256m', 'http.lowSpeedLimit=1000', 'http.lowSpeedTime=120')
    espera = 30
    for n in range(1, intentos + 1):
        r = git('push', remoto, f'HEAD:{rama}', check=False, extra_config=cfg)
        if r.returncode == 0:
            return True
        err = (r.stderr or b'').decode(errors='replace').strip()
        print(f'  push falló (intento {n}/{intentos}):\n    ' + err.replace('\n', '\n    '))
        if re.search(r'rejected|non-fast-forward|fetch first', err):
            print('  El remoto tiene commits nuevos. Corre `git pull --ff-only` y vuelve a empezar.')
            return False
        if re.search(r'Authentication|403|Permission', err, re.I):
            print('  Problema de permisos o de token: lo resuelve una persona.')
            return False
        if n < intentos:
            print(f'  Reintento en {espera} s…')
            time.sleep(espera)
            espera *= 2
    return False


def fotos_del_lote(carpeta):
    """Fotos que nombra un lote guardado como fotos (3.1.82+), o None si guarda recortes."""
    try:
        with open(os.path.join(carpeta, 'dataset.json'), encoding='utf-8') as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    if meta.get('crops_in_folder') is not False:
        return None
    rutas = []
    try:
        with open(os.path.join(carpeta, 'captures.csv'), encoding='utf-8-sig', newline='') as fh:
            for fila in csv.DictReader(fh):
                rel = fila.get('original_path') or ''
                if rel.startswith('../capturas/'):
                    rutas.append(os.path.normpath(os.path.join(carpeta, rel)).replace(os.sep, '/'))
    except OSError:
        return []
    return sorted(set(rutas))


def auditar(verificar_hash):
    try:
        with open('dataset_index.json', encoding='utf-8') as fh:
            indice = json.load(fh)
    except ValueError as e:
        print(f'dataset_index.json está dañado: {e}')
        return 1
    rastreados = {}
    en_git_set = set(git('ls-files', '-z', 'devices').stdout.decode('utf-8', 'surrogateescape').split('\0'))
    for ruta in en_git_set:
        lote = lote_de(ruta)
        if lote:
            rastreados[lote] = rastreados.get(lote, 0) + 1
    print('Estado de los lotes del índice\n')
    malos = 0
    for dev, info in sorted((indice.get('devices') or {}).items()):
        if not isinstance(info, dict):
            continue
        for lot in info.get('lots', []):
            lote = f'{dev}/{lot}'
            carpeta = os.path.join('devices', lote)
            if not os.path.isdir(carpeta):
                print(f'  FALTA       {lote}: está en el índice pero no hay carpeta')
                malos += 1
                continue
            r = revisar_lote(lote, [], False)
            en_git = rastreados.get(lote, 0)
            if not r['checksums']:
                print(f'  ANTIGUO     {lote}: sin checksums.sha256 ({en_git} archivos en Git)')
                continue
            sums = leer_checksums(carpeta)
            sin_subir = [n for n in sums if f'{carpeta}/{n}' not in en_git_set
                         and os.path.exists(os.path.join(carpeta, n))]
            if r['faltan']:
                malos += 1
                tipos = {}
                for n in r['faltan']:
                    tipos[n.split('/')[0]] = tipos.get(n.split('/')[0], 0) + 1
                det = ', '.join(f'{k}: {v}' for k, v in sorted(tipos.items(), key=lambda t: -t[1]))
                print(f'  INCOMPLETO  {lote}: faltan {len(r["faltan"])} de {len(sums)} archivos ({det})')
            elif sin_subir:
                print(f'  SIN SUBIR   {lote}: {len(sin_subir)} archivos en disco aún no están en Git')
            else:
                print(f'  COMPLETO    {lote}: {len(sums)} archivos')
            fotos = fotos_del_lote(carpeta)
            if fotos is not None:
                sin_disco = [f for f in fotos if not os.path.exists(f)]
                sin_git = [f for f in fotos if f not in en_git_set and os.path.exists(f)]
                if sin_disco:
                    malos += 1
                    print(f'              FOTOS: faltan {len(sin_disco)} de {len(fotos)} en devices/{dev}/capturas/, '
                          f'p. ej. {sin_disco[0]}')
                elif sin_git:
                    print(f'              FOTOS: {len(sin_git)} de {len(fotos)} aún no están en Git')
                else:
                    print(f'              FOTOS: {len(fotos)} en devices/{dev}/capturas/')
            if verificar_hash:
                mal = [n for n in sums if os.path.exists(os.path.join(carpeta, n))
                       and sha256(os.path.join(carpeta, n)) != sums[n]]
                if mal:
                    malos += 1
                    print(f'              {len(mal)} archivos no coinciden con su huella, p. ej. {mal[0]}')
    # Fotos completas por germinadora (3.1.82+), un checksums.sha256 por día.
    for dev in sorted(os.listdir('devices')) if os.path.isdir('devices') else []:
        raiz_fotos = os.path.join('devices', dev, 'capturas')
        if not os.path.isdir(raiz_fotos):
            continue
        n_dias = n_fotos = n_git = 0
        for dia in sorted(os.listdir(raiz_fotos)):
            carpeta = f'devices/{dev}/capturas/{dia}'
            sums = leer_checksums(carpeta)
            if sums is None:
                continue
            n_dias += 1
            n_fotos += len(sums)
            n_git += sum(1 for n in sums if f'{carpeta}/{n}' in en_git_set)
            r = revisar_carpeta(carpeta, [], False)
            if r['faltan']:
                malos += 1
                print(f'  INCOMPLETO  {carpeta}: faltan {len(r["faltan"])} de {len(sums)} fotos')
            if verificar_hash:
                mal = [n for n in sums if os.path.exists(os.path.join(carpeta, n))
                       and sha256(os.path.join(carpeta, n)) != sums[n]]
                if mal:
                    malos += 1
                    print(f'  DAÑADO      {carpeta}: {len(mal)} fotos no coinciden con su huella, p. ej. {mal[0]}')
        if n_dias:
            print(f'\nFotos de {dev}: {n_fotos} en {n_dias} días, {n_git} ya en Git')

    tam = 0
    for base, _, files in os.walk('.git'):
        for f in files:
            try:
                tam += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    print(f'\nTamaño de .git: {tam / 1024 ** 3:.2f} GB', end='')
    print('  (más de 5 GB: dejar de subir recortes por Git; ver el manifiesto)' if tam > 5 * 1024 ** 3
          else '  (más de 3 GB: planear el cambio a otro almacenamiento)' if tam > 3 * 1024 ** 3 else '')
    return 1 if malos else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--subir', action='store_true', help='hacer commit y push (sin esto solo muestra el plan)')
    ap.add_argument('--auditar', action='store_true', help='revisar cada lote del índice contra sus huellas')
    ap.add_argument('--verificar-hash', action='store_true', help='además compara el SHA-256 de cada archivo')
    ap.add_argument('--max-mb', type=int, default=400, help='peso máximo por parte (400)')
    ap.add_argument('--max-archivos', type=int, default=5000, help='archivos máximos por parte (5000)')
    ap.add_argument('--remoto', default='origin')
    ap.add_argument('--rama', default='main')
    ap.add_argument('--permitir-borrados', action='store_true', help='incluir archivos borrados (pide revisión humana)')
    ap.add_argument('--aceptar-incompletos', action='store_true', help='subir lotes a los que les faltan archivos')
    a = ap.parse_args()

    raiz()
    if a.auditar:
        sys.exit(auditar(a.verificar_hash))

    if git('diff', '--cached', '--quiet', check=False).returncode:
        raise SystemExit('Hay cambios ya preparados (staged). Revísalos y quítalos con '
                         '`git restore --staged .` antes de usar este script.')

    items = pendientes()
    partes, problemas, avisos, borrados, lotes, dias = planear(items, a.max_mb, a.max_archivos, a.permitir_borrados)

    incompletos = {}
    for lote in lotes:
        rutas = [r for p in partes for r in p['rutas'] if lote_de(r) == lote]
        r = revisar_lote(lote, rutas, a.verificar_hash or a.subir)
        if r['distintos']:
            problemas.append(f'{lote}: {len(r["distintos"])} archivos no coinciden con checksums.sha256, '
                             f'p. ej. {r["distintos"][0]}. Vuelve a guardar el lote desde la app.')
        if r['faltan']:
            incompletos[lote] = r['faltan']
    for dev, dia in dias:
        carpeta = f'devices/{dev}/capturas/{dia}'
        rutas = [x for p in partes for x in p['rutas'] if x.startswith(carpeta + '/')]
        r = revisar_carpeta(carpeta, rutas, a.verificar_hash or a.subir)
        if r['distintos']:
            problemas.append(f'{carpeta}: {len(r["distintos"])} fotos no coinciden con checksums.sha256, '
                             f'p. ej. {r["distintos"][0]}. Vuelve a guardar los lotes de ese día desde la app.')
        if r['faltan']:
            incompletos[carpeta] = r['faltan']

    total = sum(p['peso'] for p in partes)
    print(f'Pendiente: {sum(len(p["rutas"]) for p in partes)} archivos, {fmt_mb(total)}, en {len(partes)} partes\n')
    for i, p in enumerate(partes, 1):
        print(f'  {i:>3}. {p["titulo"]}: {len(p["rutas"])} archivos, {fmt_mb(p["peso"])}')
    for lote, faltan in incompletos.items():
        print(f'\nINCOMPLETO {lote}: faltan {len(faltan)} archivos de su checksums.sha256, p. ej. {faltan[0]}')
        print('  Vuelve a guardar el lote (o los lotes de ese día) desde la app en esta germinadora antes de subirlo.')
    if borrados:
        accion = 'se incluyen' if a.permitir_borrados else 'NO se incluyen (usa --permitir-borrados tras revisarlos)'
        print(f'\nBorrados: {len(borrados)}, {accion}. Ejemplo: {borrados[0]}')
    for x in avisos:
        print(f'\nAviso: {x}')
    for x in problemas:
        print(f'\nBLOQUEO: {x}')

    if not a.subir:
        print('\nNo se cambió nada. Para subir: python3 scripts/subir_por_partes.py --subir')
        return
    if problemas:
        raise SystemExit('\nNo se sube nada mientras haya bloqueos.')
    if incompletos and not a.aceptar_incompletos:
        raise SystemExit('\nHay lotes incompletos. Complétalos o usa --aceptar-incompletos si una persona lo decidió.')

    atras, adelante = estado_rama(a.remoto, a.rama)
    if atras:
        r = git('merge', '--ff-only', f'{a.remoto}/{a.rama}', check=False)
        if r.returncode:
            raise SystemExit(f'La rama local y {a.remoto}/{a.rama} se separaron. Lo resuelve una persona; '
                             'no uses --force.')
        print(f'Se trajeron {atras} commits nuevos del remoto.')
    if adelante:
        print(f'\nHay {adelante} commits locales sin subir de una corrida anterior. Subiéndolos…')
        if not push(a.remoto, a.rama):
            raise SystemExit('No se pudo subir. Vuelve a correr el script más tarde: retoma desde aquí.')

    for i, p in enumerate(partes, 1):
        print(f'\n[{i}/{len(partes)}] {p["titulo"]} ({len(p["rutas"])} archivos, {fmt_mb(p["peso"])})')
        lista = '\0'.join(p['rutas']).encode('utf-8', 'surrogateescape')
        r = subprocess.run(['git', 'add', '-A', '--pathspec-from-file=-', '--pathspec-file-nul'], input=lista,
                           stderr=subprocess.PIPE)
        if r.returncode:
            raise SystemExit(f'git add falló: {r.stderr.decode(errors="replace")}')
        if git('diff', '--cached', '--quiet', check=False).returncode == 0:
            print('  Nada que guardar en esta parte (ya estaba).')
            continue
        git('commit', '--quiet', '-m', p['titulo'])
        if not push(a.remoto, a.rama):
            raise SystemExit(f'\nSe detuvo en la parte {i}. Lo ya subido queda; el commit de esta parte queda '
                             'local y se sube primero en la siguiente corrida.')
        print('  Subida.')
    print('\nListo. Comprueba con: python3 scripts/subir_por_partes.py --auditar')


if __name__ == '__main__':
    main()
