/**
 * @fileoverview Display helpers for mr-deed chunk events (ADR-0018).
 * Each event is `{ type: 'mr_deed.<kind>', message }`, where `message` is the
 * mr-deed trajectory message. Pure functions: no Vue, no DOM.
 */

const KINDS = {
  'mr_deed.action': 'mr_deed_action',
  'mr_deed.observation': 'mr_deed_observation',
  'mr_deed.format_error': 'mr_deed_format_error',
  'mr_deed.exit': 'hidden',
}

/** Row kind for an mr-deed event, or null when the event isn't from mr-deed. */
export function classifyMrDeedEvent(event) {
  const t = event?.type
  if (typeof t !== 'string' || !t.startsWith('mr_deed.')) return null
  if (t === 'mr_deed.assistant') return mrDeedText(event) ? 'mr_deed_text' : 'hidden'
  return KINDS[t] || 'hidden'
}

export function mrDeedText(event) {
  const c = event?.message?.content
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map(b => b?.text || '').join('')
  return ''
}

export function mrDeedActionLabel(event) {
  const m = event?.message || {}
  if ((m.name || 'bash') !== 'bash') return `🔧 ${m.name}`
  return `$ ${(m.command || '').split('\n')[0].slice(0, 80)}`
}

export function mrDeedActionInput(event) {
  const m = event?.message || {}
  if ((m.name || 'bash') === 'bash') return m.command || ''
  return JSON.stringify(m.arguments || {}, null, 2)
}
