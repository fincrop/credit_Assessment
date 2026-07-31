import { NextResponse } from 'next/server';
import { INDIA_STATES } from '../../../lib/india_lgd_data';

export async function GET() {
  return NextResponse.json({ states: INDIA_STATES });
}
