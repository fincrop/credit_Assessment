import { NextRequest, NextResponse } from 'next/server';
import { getDistricts } from '../../../lib/india_lgd_data';

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const stateCode = searchParams.get('state_code');
  if (!stateCode) {
    return NextResponse.json({ error: 'state_code is required' }, { status: 400 });
  }
  const districts = getDistricts(stateCode);
  return NextResponse.json({ districts });
}
