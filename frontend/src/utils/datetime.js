/**
 * Format a UTC ISO-8601 timestamp for display in the given IANA timezone.
 *
 * Uses the browser-native Intl.DateTimeFormat — no library needed.
 * Output format: YYYY-MM-DD HH:MM:SS (sv-SE locale is unambiguous and sortable).
 *
 * @param {string|null|undefined} isoUtc - UTC ISO-8601 string, e.g. "2024-01-15T09:30:00Z"
 * @param {string} tz - IANA timezone name, e.g. "Asia/Shanghai" or "UTC"
 * @returns {string} formatted local datetime, or empty string if isoUtc is falsy
 */
export function formatInTimezone(isoUtc, tz) {
  if (!isoUtc) return ''
  try {
    const date = new Date(isoUtc)
    if (isNaN(date.getTime())) return isoUtc
    return new Intl.DateTimeFormat('sv-SE', {
      timeZone: tz || 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date)
  } catch {
    return isoUtc
  }
}
