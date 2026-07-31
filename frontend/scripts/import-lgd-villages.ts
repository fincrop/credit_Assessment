/**
 * Import LGD villages into MongoDB `lgd_villages` (and optionally talukas).
 *
 * Prerequisites:
 *   - MONGODB_URI set (frontend/.env.local or env)
 *   - scripts/lgd_tmp/village/4-village.csv present (unzip from 4-village.csv.zip)
 *
 * Usage:
 *   cd frontend
 *   npx tsx scripts/import-lgd-villages.ts
 *
 * Optional: also re-seed talukas from public/india_lgd.json into `lgd_talukas`.
 */
import fs from 'fs';
import path from 'path';
import readline from 'readline';
import { createReadStream } from 'fs';
import { MongoClient, ServerApiVersion } from 'mongodb';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');

function loadEnvLocal() {
  const envPath = path.join(ROOT, '.env.local');
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (!process.env[m[1]]) process.env[m[1]] = v;
  }
}

loadEnvLocal();

const MONGODB_URI = (process.env.MONGODB_URI || '').trim();
const DB_NAME = process.env.MONGODB_DB || process.env.MONGODB_DATABASE || 'agristack';

if (!MONGODB_URI) {
  console.error('MONGODB_URI is required. Set it in frontend/.env.local or the environment.');
  process.exit(1);
}

const VILLAGE_CSV = path.join(__dirname, 'lgd_tmp', 'village', '4-village.csv');
const LGD_JSON = path.join(ROOT, 'public', 'india_lgd.json');

function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let field = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    const n = line[i + 1];
    if (inQ) {
      if (c === '"' && n === '"') {
        field += '"';
        i++;
      } else if (c === '"') inQ = false;
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ',') {
      out.push(field);
      field = '';
    } else field += c;
  }
  out.push(field);
  return out;
}

async function seedTalukas(db: import('mongodb').Db) {
  if (!fs.existsSync(LGD_JSON)) {
    console.warn('Skip taluka seed: public/india_lgd.json missing. Run build-india-lgd-json.mjs first.');
    return;
  }
  const data = JSON.parse(fs.readFileSync(LGD_JSON, 'utf8')) as {
    talukas: Record<string, { lgd_code: string; name: string; district_lgd_code: string; state_lgd_code: string }[]>;
  };
  const docs = Object.values(data.talukas || {}).flat();
  const col = db.collection('lgd_talukas');
  await col.deleteMany({});
  if (docs.length === 0) return;
  const BATCH = 1000;
  for (let i = 0; i < docs.length; i += BATCH) {
    await col.insertMany(docs.slice(i, i + BATCH), { ordered: false });
  }
  await col.createIndex({ district_lgd_code: 1, name: 1 });
  await col.createIndex({ lgd_code: 1 }, { unique: true });
  console.log(`Seeded lgd_talukas: ${docs.length}`);
}

async function importVillages(db: import('mongodb').Db) {
  if (!fs.existsSync(VILLAGE_CSV)) {
    console.error(`Missing ${VILLAGE_CSV}`);
    console.error('Download & unzip 4-village.csv.zip into scripts/lgd_tmp/village/');
    process.exit(1);
  }

  const col = db.collection('lgd_villages');
  console.log('Clearing lgd_villages…');
  await col.deleteMany({});

  const rl = readline.createInterface({
    input: createReadStream(VILLAGE_CSV, { encoding: 'utf8' }),
    crlfDelay: Infinity,
  });

  let header: string[] | null = null;
  let batch: Record<string, string>[] = [];
  let total = 0;
  const BATCH = 2000;

  const flush = async () => {
    if (!batch.length) return;
    await col.insertMany(batch, { ordered: false });
    total += batch.length;
    process.stdout.write(`\rImported villages: ${total}`);
    batch = [];
  };

  for await (const line of rl) {
    if (!header) {
      header = parseCsvLine(line);
      continue;
    }
    if (!line.trim()) continue;
    const cols = parseCsvLine(line);
    // Header: S.No.,District Code,District Name,Subdistrict Code,Subdistrict Name,Village Code,Village Version,Village Name,...
    const doc = {
      lgd_code: String(cols[5] || '').trim(),
      name: String(cols[7] || '').trim(),
      taluka_lgd_code: String(cols[3] || '').trim(),
      district_lgd_code: String(cols[1] || '').trim(),
      state_lgd_code: String(cols[12] || '').trim(),
    };
    if (!doc.lgd_code || !doc.name) continue;
    batch.push(doc);
    if (batch.length >= BATCH) await flush();
  }
  await flush();
  console.log(`\nCreating indexes…`);
  await col.createIndex({ taluka_lgd_code: 1, name: 1 });
  await col.createIndex({ district_lgd_code: 1, name: 1 });
  // Note: Atlas apiStrict disallows text indexes — use regex queries instead
  try {
    await col.createIndex({ lgd_code: 1 }, { unique: true });
  } catch (e) {
    console.warn('lgd_code unique index:', e instanceof Error ? e.message : e);
  }
  console.log(`Done. lgd_villages: ${total}`);
}

async function main() {
  const client = new MongoClient(MONGODB_URI, {
    serverApi: { version: ServerApiVersion.v1, strict: true, deprecationErrors: true },
  });
  await client.connect();
  const db = client.db(DB_NAME);
  console.log('Connected to', DB_NAME);
  await seedTalukas(db);
  await importVillages(db);
  await client.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
