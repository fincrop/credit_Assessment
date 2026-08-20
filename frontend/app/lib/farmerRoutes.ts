/** View the latest stored analysis for a farmer. */
export function farmerResultsHref(farmerId: string): string {
  return `/dashboard?farmer_id=${encodeURIComponent(farmerId)}`;
}

/** Open plot selection and run (or re-run) an assessment. */
export function farmerAssessHref(farmerId: string): string {
  return `/dashboard?farmer_id=${encodeURIComponent(farmerId)}&mode=assess`;
}
