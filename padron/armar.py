"""Arma el padrón de RUC de Yupana a partir del "padrón reducido del RUC" que SUNAT publica gratis cada día.

Uso: python3 -I padron/armar.py <padron_reducido_ruc.zip> <carpeta de salida> [ubigeo_distrito.csv]
La tabla de ubigeos es de jmcastagnetto/ubigeo-peru-aumentado (licencia MIT), solo con sus columnas de nombres.
Corre cada lunes en GitHub Actions de jchaconv94/yupana-web (`padron.yml`).

Lo que sale (el formato lo lee `src/domain/padron.ts`; si se cambia aquí, se cambia allá):
- `b0.bin`, `b1.bin`…: pedazos de hasta 45 MB (el plan gratis de la nube acepta archivos de hasta 50 MB). Cada uno es
  una seguidilla de bloques gzip; cada bloque tiene hasta BLOQUE registros, ordenados por RUC, uno por línea:
    `salto|estado|nombre|condición|dirección`
  `salto` = el RUC menos el RUC de la línea anterior (en la primera línea, el RUC entero); `estado` vacío = ACTIVO;
  `condición` vacía = HABIDO; lo vacío del final no se escribe. De un RUC que no está activo (ni en suspensión
  temporal) va solo su estado, y si es de una persona (RUC 10), también su nombre (para buscarla por su DNI).
- `indice.tsv`: por bloque, `primer RUC <tab> archivo <tab> desde <tab> bytes`.
- `info.json`: la fecha del padrón, cuántos RUC y los archivos (con su SHA-256).
La dirección se arma como en la consulta de SUNAT ("JR. MONTERROSA NRO. 271 DPTO. 902 LIMA - LIMA - SANTIAGO DE SURCO");
los nombres de los distritos salen de la tabla de ubigeos del INEI (`ubigeo_distrito.csv`, opcional).
"""
import csv, datetime, gzip, hashlib, io, json, os, sys, zipfile

BLOQUE = 512
TOPE_ARCHIVO = 45_000_000


def distritos(path):
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding='utf8') as f:
        for r in csv.DictReader(f):
            out[r['inei']] = f"{r['departamento']} - {r['provincia']} - {r['distrito']}"
    return out


def limpio(x):
    x = x.strip()
    return '' if x in ('-', '----', '--') else x


def direccion(p, ubi):
    via, nombre, zona_cod, zona, numero, interior, lote, dpto, mz, km = (limpio(x) for x in p[5:15])
    partes = []
    if nombre:
        partes.append(f"{via} {nombre}".strip())
    if numero:
        partes.append(f"NRO. {numero}")
    if interior:
        partes.append(f"INT. {interior}")
    if dpto:
        partes.append(f"DPTO. {dpto}")
    if mz:
        partes.append(f"MZA. {mz}")
    if lote:
        partes.append(f"LOTE {lote}")
    if km:
        partes.append(f"KM. {km}")
    if zona:
        partes.append(f"{zona_cod} {zona}".strip())
    if not partes:
        return ''
    lugar = ubi.get(limpio(p[4]), '')
    return ' '.join(partes + ([lugar] if lugar else [])).replace('|', ' ')


def main():
    zpath, salida = sys.argv[1], sys.argv[2]
    ubi = distritos(sys.argv[3] if len(sys.argv) > 3 else None)
    os.makedirs(salida, exist_ok=True)
    z = zipfile.ZipFile(zpath)
    entrada = z.infolist()[0]
    fecha = datetime.date(*entrada.date_time[:3]).isoformat()
    filas = []
    sin_lugar = set()
    with z.open(entrada) as f:
        next(f)
        for raw in f:
            p = raw.decode('latin1').rstrip('\r\n').split('|')
            if len(p) < 15 or len(p[0]) != 11 or not p[0].isdigit():
                continue
            estado = '' if p[2] == 'ACTIVO' else p[2].strip()
            cond = '' if p[3] == 'HABIDO' else ('' if p[3] in ('-', '') else p[3].strip())
            u = limpio(p[4])
            if u and ubi and u not in ubi:
                sin_lugar.add(u)
            linea = [estado, p[1].strip().replace('|', ' '), cond, direccion(p, ubi)]
            # Un RUC que ya no está activo (ni en suspensión temporal) no sirve para facturar: basta su estado. De una persona
            # (RUC 10) se deja su nombre, que sirve para buscarla por su DNI.
            if estado and estado != 'SUSPENSION TEMPORAL':
                linea = linea[:2] if p[0].startswith('10') else linea[:1]
            while linea and linea[-1] == '':
                linea.pop()
            filas.append((int(p[0]), '|'.join(linea)))
    filas.sort()
    # Un RUC repetido (no debería) se queda con la última línea.
    unicos = []
    for r in filas:
        if unicos and unicos[-1][0] == r[0]:
            unicos[-1] = r
        else:
            unicos.append(r)
    filas = unicos

    indice, archivos = [], []
    actual, n, desde = None, -1, 0

    def abrir():
        nonlocal actual, n, desde
        if actual:
            actual.close()
        n += 1
        actual = open(os.path.join(salida, f'b{n}.bin'), 'wb')
        archivos.append(f'b{n}.bin')
        desde = 0

    abrir()
    for i in range(0, len(filas), BLOQUE):
        bloque = filas[i:i + BLOQUE]
        previo, lineas = 0, []
        for ruc, resto in bloque:
            lineas.append(f"{ruc - previo}|{resto}" if resto else f"{ruc - previo}")
            previo = ruc
        datos = gzip.compress('\n'.join(lineas).encode('utf8'), compresslevel=9, mtime=0)
        if desde + len(datos) > TOPE_ARCHIVO:
            abrir()
        actual.write(datos)
        indice.append((bloque[0][0], n, desde, len(datos)))
        desde += len(datos)
    actual.close()

    with open(os.path.join(salida, 'indice.tsv'), 'w') as f:
        for r in indice:
            f.write('\t'.join(str(x) for x in r) + '\n')
    detalle = []
    for a in archivos:
        b = open(os.path.join(salida, a), 'rb').read()
        detalle.append({'nombre': a, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()})
    info = {'fecha': fecha, 'rucs': len(filas), 'bloques': len(indice), 'archivos': detalle, 'sin_distrito': sorted(sin_lugar)[:50]}
    with open(os.path.join(salida, 'info.json'), 'w') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    total = sum(d['bytes'] for d in detalle)
    print(f"padrón del {fecha}: {len(filas)} RUC en {len(indice)} bloques, {len(archivos)} archivos, {total / 1e6:.1f} MB; ubigeos sin nombre: {len(sin_lugar)}")


if __name__ == '__main__':
    main()
