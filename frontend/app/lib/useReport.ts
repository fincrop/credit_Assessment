'use client';

import { useEffect, useState } from 'react';
import { fetchReport, type ReportResult } from './reportClient';
import type { ReportPayload } from '../types/report';

type ReportFailure = Extract<ReportResult, { ok: false }>;

export interface ReportState {
  report: ReportPayload | null;
  loading: boolean;
  /** Non-null only when the failure is worth telling the user about. */
  problem: { kind: ReportFailure['kind']; message: string } | null;
}

/**
 * Fetch the report payload for a farmer (optionally one plot).
 *
 * The evidence series — the NDVI trajectory and per-bin provenance — lives in
 * the `evidence` collection, not on the job result, so it is only reachable
 * through this endpoint. Panels that need it degrade to their own empty state
 * rather than blocking the page: a missing evidence record is normal for an
 * assessment saved before evidence existed, and the score is still valid.
 *
 * `not-found` and `not-configured` are deliberately NOT surfaced as errors.
 * A farmer with no stored report is an ordinary state, and an unset
 * PIPELINE_API_URL is a deployment fact the loan officer cannot act on.
 */
export function useReport(
  farmerId: string | null | undefined,
  opts?: { plotKey?: string; enabled?: boolean }
): ReportState {
  const enabled = opts?.enabled !== false;
  const plotKey = opts?.plotKey;
  const id = (farmerId || '').trim();

  const [state, setState] = useState<ReportState>({
    report: null,
    loading: false,
    problem: null,
  });

  useEffect(() => {
    if (!enabled || !id) {
      setState({ report: null, loading: false, problem: null });
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, problem: null }));

    (async () => {
      const result = await fetchReport(id, { plotKey, signal: controller.signal });
      if (cancelled) return;

      if (result.ok) {
        setState({ report: result.report, loading: false, problem: null });
        return;
      }

      setState({
        report: null,
        loading: false,
        problem:
          result.kind === 'not-found' || result.kind === 'not-configured'
            ? null
            : { kind: result.kind, message: result.message },
      });
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [id, plotKey, enabled]);

  return state;
}
