// Business-date helpers. All dates are yyyy-mm-dd Asia/Riyadh calendar dates.

export function addDays(d: string, n: number): string {
  const t = new Date(`${d}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000) + 1;
}

export type Preset = { key: string; label: string; days: number | null };
export const PRESETS: Preset[] = [
  { key: "7d", label: "Last 7 days", days: 7 },
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
  { key: "all", label: "All data", days: null },
];

/** Presets are anchored to the latest date in the data, not today (the dataset is historical). */
export function presetRange(p: Preset, minDate: string, maxDate: string): { from: string; to: string } {
  if (p.days === null) return { from: minDate, to: maxDate };
  const from = addDays(maxDate, -(p.days - 1));
  return { from: from < minDate && p.days > 90 ? minDate : from, to: maxDate };
}
