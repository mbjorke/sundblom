const WEEKDAYS = ['Söndagen','Måndagen','Tisdagen','Onsdagen','Torsdagen','Fredagen','Lördagen'];
const MONTHS   = ['','januari','februari','mars','april','maj','juni',
                   'juli','augusti','september','oktober','november','december'];

/**
 * Format an ISO date string (YYYY-MM-DD) to Swedish long-form date.
 * e.g. "2026-03-19" → "Torsdagen den 19 mars 2026"
 */
export function formatSwedishDate(isoDate: string): string {
  // Parse as UTC noon to avoid timezone edge cases
  const d = new Date(isoDate + 'T12:00:00Z');
  return `${WEEKDAYS[d.getUTCDay()]} den ${d.getUTCDate()} ${MONTHS[d.getUTCMonth() + 1]} ${d.getUTCFullYear()}`;
}

/**
 * Convert double-newline separated plain text to HTML paragraphs.
 */
export function toParagraphs(text: string): string {
  return text
    .split(/\n\n+/)
    .map(p => p.trim())
    .filter(p => p.length > 0)
    .map(p => `<p>${p}</p>`)
    .join('');
}

/**
 * Extract first ~155 characters from text for use as meta description.
 */
export function toDescription(text: string): string {
  const first = text.split('\n\n')[0]?.trim() ?? '';
  return first.length > 155 ? first.slice(0, 152) + '…' : first;
}
