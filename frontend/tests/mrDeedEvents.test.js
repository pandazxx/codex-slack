import { describe, it, expect } from 'vitest'
import { classifyMrDeedEvent, mrDeedText, mrDeedActionLabel, mrDeedActionInput } from '../src/lib/mrDeedEvents.js'

const ev = (kind, message) => ({ type: `mr_deed.${kind}`, message })

describe('classifyMrDeedEvent', () => {
  it('maps every mr-deed kind to its display kind', () => {
    expect(classifyMrDeedEvent(ev('assistant', { content: 'plan' }))).toBe('mr_deed_text')
    expect(classifyMrDeedEvent(ev('action', { name: 'bash', command: 'ls' }))).toBe('mr_deed_action')
    expect(classifyMrDeedEvent(ev('observation', { content: 'out' }))).toBe('mr_deed_observation')
    expect(classifyMrDeedEvent(ev('format_error', { content: 'bad' }))).toBe('mr_deed_format_error')
    expect(classifyMrDeedEvent(ev('exit', { extra: { exit_status: 'Submitted' } }))).toBe('hidden')
  })

  it('hides assistant messages without text and unknown mr-deed kinds', () => {
    expect(classifyMrDeedEvent(ev('assistant', { content: null }))).toBe('hidden')
    expect(classifyMrDeedEvent(ev('future_kind', {}))).toBe('hidden')
  })

  it('returns null for events from other adapters', () => {
    expect(classifyMrDeedEvent({ type: 'assistant', message: { content: [] } })).toBeNull()
    expect(classifyMrDeedEvent({ type: 'item.completed' })).toBeNull()
    expect(classifyMrDeedEvent({})).toBeNull()
  })
})

describe('mr-deed text and action helpers', () => {
  it('reads string and block content', () => {
    expect(mrDeedText(ev('observation', { content: 'hello' }))).toBe('hello')
    expect(mrDeedText(ev('assistant', { content: [{ text: 'a' }, { text: 'b' }] }))).toBe('ab')
    expect(mrDeedText(ev('assistant', {}))).toBe('')
  })

  it('labels bash actions by their first command line and host tools by name', () => {
    expect(mrDeedActionLabel(ev('action', { name: 'bash', command: 'echo one\necho two' }))).toBe('$ echo one')
    expect(mrDeedActionLabel(ev('action', { name: 'delegate_task', arguments: { staff: 'eng' } }))).toBe('🔧 delegate_task')
  })

  it('shows the bash command or the host-tool arguments', () => {
    expect(mrDeedActionInput(ev('action', { name: 'bash', command: 'ls -la' }))).toBe('ls -la')
    expect(mrDeedActionInput(ev('action', { name: 'notes', arguments: { key: 'k' } }))).toBe('{\n  "key": "k"\n}')
  })
})
