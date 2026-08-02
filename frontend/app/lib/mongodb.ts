import dns from 'dns';
import { Resolver } from 'dns/promises';
import { MongoClient, Db, ServerApiVersion } from 'mongodb';

/**
 * Windows / corporate DNS often fails `querySrv` for mongodb+srv:// (ETIMEOUT).
 * Prefer public resolvers for Node DNS before the driver connects.
 */
try {
  const current = dns.getServers();
  const prefer = ['8.8.8.8', '1.1.1.1', '8.8.4.4'];
  const merged = [...prefer, ...current.filter((s) => !prefer.includes(s))];
  dns.setServers(merged);
} catch {
  /* ignore */
}

try {
  dns.setDefaultResultOrder('ipv4first');
} catch {
  /* older Node */
}

declare global {
  // Persist across Next.js hot reloads in development
  // eslint-disable-next-line no-var
  var __agriMongo: {
    client: MongoClient | null;
    db: Db | null;
    connecting: Promise<{ client: MongoClient; db: Db }> | null;
    resolvedUri: string | null;
  } | undefined;
}

function store() {
  if (!global.__agriMongo) {
    global.__agriMongo = {
      client: null,
      db: null,
      connecting: null,
      resolvedUri: null,
    };
  }
  return global.__agriMongo;
}

function isTransientDnsError(msg: string): boolean {
  return (
    msg.includes('querySrv') ||
    msg.includes('ETIMEOUT') ||
    msg.includes('ECONNREFUSED') ||
    msg.includes('ENOTFOUND') ||
    msg.includes('ESERVFAIL') ||
    msg.includes('MongoServerSelectionError') ||
    msg.includes('DNS / SRV')
  );
}

/**
 * Resolve mongodb+srv:// via public DNS ourselves, then hand the driver a
 * standard mongodb:// multi-host URI. Avoids driver's querySrv hitting broken
 * OS / VPN resolvers (common cause of querySrv ETIMEOUT on Windows).
 */
async function toDirectMongoUri(uri: string): Promise<string> {
  if (!uri.startsWith('mongodb+srv://')) return uri;

  const withoutScheme = uri.slice('mongodb+srv://'.length);
  const at = withoutScheme.lastIndexOf('@');
  const auth = at >= 0 ? withoutScheme.slice(0, at + 1) : '';
  const hostAndRest = at >= 0 ? withoutScheme.slice(at + 1) : withoutScheme;
  const slash = hostAndRest.indexOf('/');
  const qmark = hostAndRest.indexOf('?');
  let hostEnd = hostAndRest.length;
  if (slash >= 0) hostEnd = Math.min(hostEnd, slash);
  if (qmark >= 0) hostEnd = Math.min(hostEnd, qmark);
  const host = hostAndRest.slice(0, hostEnd);
  const pathAndQuery = hostAndRest.slice(hostEnd);

  const resolver = new Resolver();
  resolver.setServers(['8.8.8.8', '1.1.1.1', '8.8.4.4']);

  const srv = await resolver.resolveSrv(`_mongodb._tcp.${host}`);
  if (!srv.length) {
    throw new Error(`No SRV records for ${host}`);
  }
  const hosts = srv
    .sort((a, b) => a.priority - b.priority || b.weight - a.weight)
    .map((r) => `${r.name}:${r.port}`)
    .join(',');

  let txtOpts = '';
  try {
    const txt = await resolver.resolveTxt(host);
    txtOpts = txt
      .map((chunks) => chunks.join(''))
      .join('&')
      .replace(/^&+/, '');
  } catch {
    /* TXT optional */
  }

  // Path may be "/db" or "/db?foo=bar" or "?foo=bar" or empty
  let path = '';
  let query = '';
  if (pathAndQuery.startsWith('?')) {
    query = pathAndQuery.slice(1);
  } else if (pathAndQuery.startsWith('/')) {
    const qi = pathAndQuery.indexOf('?');
    if (qi >= 0) {
      path = pathAndQuery.slice(0, qi);
      query = pathAndQuery.slice(qi + 1);
    } else {
      path = pathAndQuery;
    }
  }

  const parts = [
    'tls=true',
    txtOpts,
    query,
    // seed list from SRV — driver still needs replicaSet from TXT when present
  ].filter(Boolean);
  const qs = parts.join('&').replace(/&&+/g, '&').replace(/^&|&$/g, '');

  return `mongodb://${auth}${hosts}${path || '/'}${qs ? `?${qs}` : ''}`;
}

/**
 * Shared Mongo client for Next.js API routes.
 * Single-flight connect avoids stampeding Atlas DNS when polls overlap.
 */
export async function connectToDatabase(): Promise<{ client: MongoClient; db: Db }> {
  const MONGODB_URI = (process.env.MONGODB_URI || '').trim();
  const MONGODB_DB =
    process.env.MONGODB_DB || process.env.MONGODB_DATABASE || 'agristack';

  if (!MONGODB_URI) {
    throw new Error(
      'MONGODB_URI is not set. For local dev add it to frontend/.env.local; for production set it in your host (e.g. Render) environment variables.'
    );
  }

  const s = store();

  if (s.client && s.db) {
    return { client: s.client, db: s.db };
  }

  if (s.connecting) {
    return s.connecting;
  }

  s.connecting = (async () => {
    let connectUri = s.resolvedUri;
    if (!connectUri) {
      try {
        connectUri = await toDirectMongoUri(MONGODB_URI);
        s.resolvedUri = connectUri;
        if (connectUri !== MONGODB_URI) {
          console.log('✅ Resolved mongodb+srv via public DNS → direct hosts');
        }
      } catch (resolveErr) {
        console.warn(
          'SRV pre-resolve failed; falling back to driver mongodb+srv:',
          resolveErr instanceof Error ? resolveErr.message : resolveErr
        );
        connectUri = MONGODB_URI;
      }
    }

    const client = new MongoClient(connectUri, {
      serverApi: {
        version: ServerApiVersion.v1,
        strict: true,
        deprecationErrors: true,
      },
      tls: true,
      tlsAllowInvalidCertificates: process.env.NODE_ENV !== 'production',
      serverSelectionTimeoutMS: 15000,
      connectTimeoutMS: 15000,
      maxPoolSize: 10,
      family: 4,
    });

    try {
      await client.connect();
      const db = client.db(MONGODB_DB);
      s.client = client;
      s.db = db;
      console.log('✅ Connected to MongoDB Atlas:', MONGODB_DB);
      return { client, db };
    } catch (err: unknown) {
      try {
        await client.close();
      } catch {
        /* ignore */
      }
      s.client = null;
      s.db = null;
      // Allow retry with fresh SRV resolve next time
      s.resolvedUri = null;
      const msg = err instanceof Error ? err.message : String(err);
      if (isTransientDnsError(msg)) {
        const e = new Error(
          `MongoDB connection failed (DNS / SRV): ${msg}. ` +
            'If `MONGODB_URI` uses `mongodb+srv://`, this machine must resolve SRV records (port 53 DNS). ' +
            'Fix: allow outbound DNS/VPN, set a public DNS (e.g. 8.8.8.8), or use Atlas’ standard `mongodb://host1:27017,...` connection string instead of SRV.'
        );
        (e as Error & { transient?: boolean }).transient = true;
        throw e;
      }
      throw err;
    } finally {
      s.connecting = null;
    }
  })();

  return s.connecting;
}

export function isMongoTransientError(err: unknown): boolean {
  if (!err) return false;
  if (
    typeof err === 'object' &&
    err !== null &&
    'transient' in err &&
    (err as { transient?: boolean }).transient
  ) {
    return true;
  }
  const msg = err instanceof Error ? err.message : String(err);
  return isTransientDnsError(msg);
}
