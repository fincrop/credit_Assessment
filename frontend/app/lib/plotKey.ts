/**
 * Client-side plot_key assignment — must match backend assign_plot_keys rules.
 */
export function assignPlotKeysClient(
  farms: Record<string, unknown>[]
): Record<string, unknown>[] {
  const seen = new Set<string>();
  return farms.map((farm, i) => {
    const f = { ...farm };
    const survey = [f.survey_number, f.sub_survey_number]
      .map((x) => String(x || '').trim())
      .filter(Boolean)
      .join('_');
    let raw =
      String(f.farm_id || '').trim() || survey || `plot_${i}`;
    let key = raw;
    if (seen.has(key)) key = `${raw}_${i}`;
    seen.add(key);
    f.plot_key = key;
    if (!f.farm_id) f.farm_id = key;
    return f;
  });
}

export function plotKeyOf(row: {
  plot_key?: string | null;
  farm_id?: string | null;
}): string {
  return String(row.plot_key || row.farm_id || '').trim();
}
