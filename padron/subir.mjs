// Sube a la nube el padrón que armó `padron/armar.py` (la carpeta de salida) por la función `padron`:
// preparar → subir cada archivo con su enlace → el índice por partes → publicar.
// En GitHub Actions se identifica con el token OIDC del flujo (`id-token: write`); desde una computadora, con la llave de
// servicio en la variable PADRON_LLAVE (nunca escrita en un archivo).
// Uso: node padron/subir.mjs <carpeta>
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const FUNCION = 'https://nvcgclopvgectkymvite.supabase.co/functions/v1/padron';
const dir = process.argv[2];
if (!dir) throw new Error('Falta la carpeta del padrón');

async function token() {
  if (process.env.PADRON_LLAVE) return process.env.PADRON_LLAVE;
  const url = process.env.ACTIONS_ID_TOKEN_REQUEST_URL;
  const pedido = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;
  if (!url || !pedido) throw new Error('Sin PADRON_LLAVE ni token de GitHub');
  const r = await fetch(`${url}&audience=yupana-padron`, { headers: { Authorization: `Bearer ${pedido}` } });
  if (!r.ok) throw new Error(`GitHub no dio el token: ${r.status}`);
  return (await r.json()).value;
}

async function llamar(body, intentos = 4) {
  for (let i = 1; ; i++) {
    try {
      const r = await fetch(FUNCION, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${await token()}` },
        body: JSON.stringify(body),
      });
      const json = await r.json().catch(() => ({}));
      if (json.ok) return json;
      // Lo que la función rechaza por los datos no se reintenta.
      if (r.status === 400 || r.status === 403 || r.status === 404 || i >= intentos) throw new Error(`${body.action}: ${r.status} ${JSON.stringify(json)}`);
    } catch (e) {
      if (i >= intentos || /: (400|403|404) /.test(String(e.message))) throw e;
    }
    await new Promise((ok) => setTimeout(ok, 3000 * i));
  }
}

const info = JSON.parse(readFileSync(join(dir, 'info.json'), 'utf8'));
const filas = readFileSync(join(dir, 'indice.tsv'), 'utf8')
  .trim()
  .split('\n')
  .map((l) => l.split('\t').map(Number));
if (filas.length !== info.bloques) throw new Error('El índice no tiene todos los bloques');

const prep = await llamar({ action: 'preparar', fecha: info.fecha, rucs: info.rucs, bloques: info.bloques, archivos: info.archivos });
console.log(`versión ${prep.version}: ${info.rucs} RUC, ${info.archivos.length} archivos`);

for (const { nombre, url } of prep.subir) {
  const datos = readFileSync(join(dir, nombre));
  for (let i = 1; ; i++) {
    const r = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'x-upsert': 'true' }, body: datos });
    if (r.ok) break;
    if (i >= 4) throw new Error(`${nombre}: ${r.status} ${(await r.text()).slice(0, 300)}`);
    await new Promise((ok) => setTimeout(ok, 5000 * i));
  }
  console.log(`subido ${nombre} (${(datos.length / 1e6).toFixed(1)} MB)`);
}

for (let i = 0; i < filas.length; i += 4000) {
  await llamar({ action: 'indice', version: prep.version, filas: filas.slice(i, i + 4000) });
}
console.log(`índice: ${filas.length} bloques`);

const fin = await llamar({ action: 'publicar', version: prep.version });
console.log(`listo: padrón del ${fin.fecha} publicado (${fin.version})`);
