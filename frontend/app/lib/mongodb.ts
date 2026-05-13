import { MongoClient, Db, ServerApiVersion } from 'mongodb';

const MONGODB_URI = process.env.MONGODB_URI || '';
const MONGODB_DB = process.env.MONGODB_DB || process.env.MONGODB_DATABASE || 'agristack';

if (!MONGODB_URI) {
    throw new Error('Please define MONGODB_URI in .env.local');
}

let cachedClient: MongoClient | null = null;
let cachedDb: Db | null = null;

export async function connectToDatabase(): Promise<{ client: MongoClient; db: Db }> {
    if (cachedClient && cachedDb) {
        return { client: cachedClient, db: cachedDb };
    }

    const client = new MongoClient(MONGODB_URI, {
        serverApi: {
            version: ServerApiVersion.v1,
            strict: true,
            deprecationErrors: true,
        },
        tls: true,
        tlsAllowInvalidCertificates: process.env.NODE_ENV !== 'production'
    });

    try {
        await client.connect();
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (
            msg.includes('querySrv') ||
            msg.includes('ECONNREFUSED') ||
            msg.includes('ENOTFOUND')
        ) {
            throw new Error(
                `MongoDB connection failed (DNS / SRV): ${msg}. ` +
                    'If `MONGODB_URI` uses `mongodb+srv://`, this machine must resolve SRV records (port 53 DNS). ' +
                    'Fix: allow outbound DNS/VPN, set a public DNS (e.g. 8.8.8.8), or use Atlas’ standard `mongodb://host1:27017,...` connection string instead of SRV.'
            );
        }
        throw err;
    }
    const db = client.db(MONGODB_DB);

    cachedClient = client;
    cachedDb = db;

    console.log('✅ Connected to MongoDB Atlas:', MONGODB_DB);
    return { client, db };
}
