import fs from 'fs';
import { MongoClient } from 'mongodb';

const env = Object.fromEntries(
  fs
    .readFileSync(
      'C:/Users/gopik/Downloads/agri_credit_pipeline/frontend/.env.local',
      'utf8'
    )
    .split(/\r?\n/)
    .filter((l) => l && !l.startsWith('#') && l.includes('='))
    .map((l) => {
      const i = l.indexOf('=');
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    })
);

const ids = [
  '2f39aa2d-d5b9-4569-85af-e5dec914375f',
  '494d163a-4de6-4015-99f9-f6adf98fd786',
  'ca629b9d-6b23-4c7f-b52d-9333eb104678',
];

const client = new MongoClient(env.MONGODB_URI);
await client.connect();
const db = client.db(env.MONGODB_DB || env.MONGODB_DATABASE || 'agristack');

for (const collName of [
  'webhook_farmers_responses',
  'webhook_responses',
  'webhook_kdss_responses',
]) {
  const col = db.collection(collName);
  for (const cid of ids) {
    const hits = await col
      .find({
        $or: [
          { 'body.message.correlation_id': cid },
          { 'body.correlation_id': cid },
          { rawBody: { $regex: cid } },
        ],
      })
      .limit(2)
      .toArray();
    if (hits.length) {
      console.log(
        JSON.stringify({
          collection: collName,
          correlation_id: cid,
          count: hits.length,
          via: hits[0].via || null,
          receivedAt: hits[0].receivedAt,
          bodyKeys: hits[0].body ? Object.keys(hits[0].body) : [],
        })
      );
    }
  }
}

const latest = await db
  .collection('webhook_farmers_responses')
  .find({ via: 'lambda-ap-south-1' })
  .sort({ receivedAt: -1 })
  .limit(5)
  .toArray();
console.log('lambda_via_docs=' + latest.length);
for (const d of latest) {
  console.log(
    JSON.stringify({
      id: String(d._id),
      receivedAt: d.receivedAt,
      correlation: d.body?.message?.correlation_id || null,
      test: !!d.body?.message?.test,
      note: d.body?.message?.note || null,
    })
  );
}

await client.close();
