import { NextRequest, NextResponse } from 'next/server';
import { connectToDatabase } from '../../../lib/mongodb';
import { verifyJWT } from '../../../lib/jwt';
import { ObjectId } from 'mongodb';

const TARGET_DB = process.env.MONGODB_DATABASE || process.env.MONGODB_DB || 'agristack';
const COLLECTION = 'farmer_farms';

export async function GET(req: NextRequest, context: { params: Promise<{ farmer_id: string }> }) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { farmer_id } = await context.params;
  if (!ObjectId.isValid(farmer_id)) return NextResponse.json({ error: 'Invalid farmer_id' }, { status: 400 });

  const { client } = await connectToDatabase();
  const db = client.db(TARGET_DB);
  const doc = await db.collection(COLLECTION).findOne({ _id: new ObjectId(farmer_id) });
  if (!doc) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json({ success: true, farmer: { ...doc, _id: doc._id.toString() } });
}

export async function PATCH(req: NextRequest, context: { params: Promise<{ farmer_id: string }> }) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { farmer_id } = await context.params;
  if (!ObjectId.isValid(farmer_id)) return NextResponse.json({ error: 'Invalid farmer_id' }, { status: 400 });

  try {
    const body = await req.json();
    const allowed = [
      'farmer_name', 'phone', 'language', 'agristack_farmer_id', 'location',
      'farms', 'historical_data', 'farmer_benefits', 'irrigation_type', 'soil_type', 'notes',
    ] as const;

    const $set: Record<string, unknown> = { updated_at: new Date() };
    for (const key of allowed) {
      if (body[key] !== undefined) $set[key] = body[key];
    }

    const { client } = await connectToDatabase();
    const db = client.db(TARGET_DB);
    const result = await db.collection(COLLECTION).findOneAndUpdate(
      { _id: new ObjectId(farmer_id) },
      { $set },
      { returnDocument: 'after' }
    );

    if (!result) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    return NextResponse.json({ success: true, farmer: { ...result, _id: result._id.toString() } });
  } catch (error) {
    console.error('PATCH /api/farms error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ farmer_id: string }> }) {
  const token = req.cookies.get('auth-token')?.value;
  if (!token) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const jwtPayload = await verifyJWT(token);
  if (!jwtPayload) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { farmer_id } = await context.params;
  if (!ObjectId.isValid(farmer_id)) return NextResponse.json({ error: 'Invalid farmer_id' }, { status: 400 });

  const { client } = await connectToDatabase();
  const db = client.db(TARGET_DB);
  await db.collection(COLLECTION).deleteOne({ _id: new ObjectId(farmer_id) });
  return NextResponse.json({ success: true });
}
