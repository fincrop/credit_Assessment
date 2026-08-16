import type { JWTPayload } from './jwt';

/** Mongo filter matching docs owned by the dashboard user. */
export function ownerFilter(user: Pick<JWTPayload, 'id' | 'email'>): Record<string, unknown> {
  const clauses: Record<string, unknown>[] = [];
  if (user.email) clauses.push({ created_by: user.email });
  if (user.id) clauses.push({ user_id: user.id });
  if (clauses.length === 0) return { created_by: '__none__' };
  if (clauses.length === 1) return clauses[0];
  return { $or: clauses };
}

export function ownerFields(user: Pick<JWTPayload, 'id' | 'email'>): {
  created_by: string;
  user_id: string;
} {
  return {
    created_by: user.email || '',
    user_id: user.id || '',
  };
}

/**
 * Does this dashboard user own this farmer?
 *
 * Lives here rather than in a route because it is an AUTHORIZATION check and
 * every route that reads farmer data must ask the same question. A copy of
 * this logic per route drifts, and the drift is only discovered when one route
 * has been leaking a farmer's record to another lender for a month.
 *
 * A farmer can be reached three ways, so all three are checked:
 *   - `farm_info.farmer_id`               (ingested AgriStack record)
 *   - `farmer_farms.agristack_farmer_id`  (onboarding journey, linked)
 *   - `farmer_farms._id`                  (journey with no AgriStack id yet)
 */
export async function isFarmerOwnedBy(
  farmerId: string,
  user: Pick<JWTPayload, 'id' | 'email'>,
  db: {
    collection: (name: string) => {
      findOne: (
        filter: Record<string, unknown>,
        options?: Record<string, unknown>
      ) => Promise<unknown>;
    };
  }
): Promise<boolean> {
  const id = String(farmerId || '').trim();
  if (!id) return false;
  const ownership = ownerFilter(user);

  const farmInfo = await db
    .collection('farm_info')
    .findOne({ farmer_id: id, ...ownership }, { projection: { _id: 1 } });
  if (farmInfo) return true;

  const journey = await db
    .collection('farmer_farms')
    .findOne({ agristack_farmer_id: id, ...ownership }, { projection: { _id: 1 } });
  if (journey) return true;

  try {
    const { ObjectId } = await import('mongodb');
    if (ObjectId.isValid(id)) {
      const byId = await db
        .collection('farmer_farms')
        .findOne({ _id: new ObjectId(id), ...ownership }, { projection: { _id: 1 } });
      if (byId) return true;
    }
  } catch {
    /* ObjectId construction can throw on malformed input — not ownership. */
  }

  return false;
}
