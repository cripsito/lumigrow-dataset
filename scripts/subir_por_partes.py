#!/usr/bin/env python3
"""Sube a GitHub los cambios del dataset en partes pequeñas y en orden seguro.

Lee MANIFIESTO-SUBIDA.md antes de usarlo. Solo usa la biblioteca estándar de
Python 3.8+ y Git 2.26+, así que corre igual en la germinadora (Raspberry) que
en una Mac.

  python3 scripts/subir_por_partes.py --auditar   estado de cada lote (no cambia nada)
  python3 scripts/subir_por_partes.py             plan de partes (no cambia nada)
  python3 scripts/subir_por_partes.py --subir     commit y push, parte por parte

Orden de cada lote: primero los recortes (crops/), después los datos del lote
(CSV, JSON, lote.db) y al final el índice general. Así el índice nunca apunta a
un lote que no llegó completo.

Nunca borra archivos, nunca reescribe la historia y nunca usa --force. Si una
subida falla, los commits quedan locales; al volver a correrlo, primero sube
esos y luego sigue con lo pendiente.
"""
import argparse
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
    (re.compile(r'(^|/)capturas/'), 'foto original (capturas/): va por USB o rsync, no por Git'),
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
    carpeta = os.path.join('devices', lote)
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


def planear(items, max_mb, max_archivos, permitir_borrados):
    problemas, avisos = [], []
    por_lote, general, borrados = {}, [], []
    for estado, ruta in items:
        if 'D' in estado:
            borrados.append(ruta)
            if not permitir_borrados:
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
        lote = lote_de(ruta)
        if lote:
            por_lote.setdefault(lote, {'pesados': [], 'datos': []})
            clave = 'pesados' if ruta.split('/')[3] == 'crops' else 'datos'
            por_lote[lote][clave].append((ruta, size))
        else:
            general.append((ruta, size))

    partes = []

    def trocear(lista, titulo):
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
            partes.append({'titulo': f'{titulo}{sufijo}', 'rutas': rutas, 'peso': peso})

    for lote in sorted(por_lote):
        nombre = lote.split('/')[1]
        trocear(por_lote[lote]['pesados'], f'Datos {nombre}: recortes')
        trocear(por_lote[lote]['datos'], f'Datos {nombre}: tablas del lote')
    trocear(general, 'Índice y vistas generales del dataset')
    return partes, problemas, avisos, borrados, sorted(por_lote)


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
            if verificar_hash:
                mal = [n for n in sums if os.path.exists(os.path.join(carpeta, n))
                       and sha256(os.path.join(carpeta, n)) != sums[n]]
                if mal:
                    malos += 1
                    print(f'              {len(mal)} archivos no coinciden con su huella, p. ej. {mal[0]}')
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
    partes, problemas, avisos, borrados, lotes = planear(items, a.max_mb, a.max_archivos, a.permitir_borrados)

    incompletos = {}
    for lote in lotes:
        rutas = [r for p in partes for r in p['rutas'] if lote_de(r) == lote]
        r = revisar_lote(lote, rutas, a.verificar_hash or a.subir)
        if r['distintos']:
            problemas.append(f'{lote}: {len(r["distintos"])} archivos no coinciden con checksums.sha256, '
                             f'p. ej. {r["distintos"][0]}. Vuelve a guardar el lote desde la app.')
        if r['faltan']:
            incompletos[lote] = r['faltan']

    total = sum(p['peso'] for p in partes)
    print(f'Pendiente: {sum(len(p["rutas"]) for p in partes)} archivos, {fmt_mb(total)}, en {len(partes)} partes\n')
    for i, p in enumerate(partes, 1):
        print(f'  {i:>3}. {p["titulo"]}: {len(p["rutas"])} archivos, {fmt_mb(p["peso"])}')
    for lote, faltan in incompletos.items():
        print(f'\nINCOMPLETO {lote}: faltan {len(faltan)} archivos de su checksums.sha256, p. ej. {faltan[0]}')
        print('  Vuelve a guardar el lote desde la app en esta germinadora antes de subirlo.')
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
