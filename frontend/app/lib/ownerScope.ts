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
