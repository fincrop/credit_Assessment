/**
 * Build public/india_lgd.json + app/data/india_lgd.json from LGD CSVs.
 * Run: node scripts/build-india-lgd-json.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TMP = path.join(__dirname, 'lgd_tmp');
const ROOT = path.join(__dirname, '..');

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const n = text[i + 1];
    if (inQ) {
      if (c === '"' && n === '"') {
        field += '"';
        i++;
      } else if (c === '"') inQ = false;
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || (c === '\r' && n === '\n')) {
      if (c === '\r') i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (c !== '\r') field += c;
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function titleCase(s) {
  return String(s || '')
    .trim()
    .replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .replace(/\bAnd\b/g, 'and')
    .replace(/\bOf\b/g, 'of');
}

const statesRaw = parseCsv(fs.readFileSync(path.join(TMP, '1-state.csv'), 'utf8'));
const districtsRaw = parseCsv(fs.readFileSync(path.join(TMP, '2-district.csv'), 'utf8'));
const talukasRaw = parseCsv(fs.readFileSync(path.join(TMP, '3-subdistrict.csv'), 'utf8'));

const states = [];
for (let i = 1; i < statesRaw.length; i++) {
  const r = statesRaw[i];
  if (!r?.[1]) continue;
  states.push({
    lgd_code: String(r[1]).trim(),
    name: titleCase(String(r[3] || r[4]).trim()),
  });
}
states.sort((a, b) => a.name.localeCompare(b.name));

const districts = {};
for (let i = 1; i < districtsRaw.length; i++) {
  const r = districtsRaw[i];
  const stateCode = String(r[0] || '').trim();
  const distCode = String(r[2] || '').trim();
  const name = String(r[3] || '').trim();
  if (!stateCode || !distCode) continue;
  if (!districts[stateCode]) districts[stateCode] = [];
  districts[stateCode].push({
    lgd_code: distCode,
    name: titleCase(name),
    state_lgd_code: stateCode,
  });
}
for (const k of Object.keys(districts)) {
  districts[k].sort((a, b) => a.name.localeCompare(b.name));
}

const talukas = {};
for (let i = 1; i < talukasRaw.length; i++) {
  const r = talukasRaw[i];
  const stateCode = String(r[1] || '').trim();
  const distCode = String(r[3] || '').trim();
  const talCode = String(r[5] || '').trim();
  const name = String(r[7] || '').trim();
  if (!distCode || !talCode) continue;
  if (!talukas[distCode]) talukas[distCode] = [];
  talukas[distCode].push({
    lgd_code: talCode,
    name: titleCase(name),
    district_lgd_code: distCode,
    state_lgd_code: stateCode,
  });
}
for (const k of Object.keys(talukas)) {
  talukas[k].sort((a, b) => a.name.localeCompare(b.name));
}

const out = {
  source: 'planemad/india-local-government-directory (LGD dump)',
  generated_at: new Date().toISOString(),
  states,
  districts,
  talukas,
};

const json = JSON.stringify(out);
fs.mkdirSync(path.join(ROOT, 'public'), { recursive: true });
fs.mkdirSync(path.join(ROOT, 'app', 'data'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'public', 'india_lgd.json'), json);
fs.writeFileSync(path.join(ROOT, 'app', 'data', 'india_lgd.json'), json);

console.log('states', states.length);
console.log(
  'districts',
  Object.values(districts).flat().length,
  'across',
  Object.keys(districts).length,
  'states'
);
console.log(
  'talukas',
  Object.values(talukas).flat().length,
  'across',
  Object.keys(talukas).length,
  'districts'
);
console.log('wrote public/india_lgd.json and app/data/india_lgd.json');
