/**
 * Unit tests for the clipboard paste image handler in TopicChat.vue.
 *
 * Strategy: mount TopicChat with the minimum set of stubs/mocks needed to
 * keep vue-router and the network layer quiet, then dispatch synthetic
 * ClipboardEvents on the textarea and assert on the component's internal
 * state (selectedFiles) and on whether preventDefault was invoked.
 *
 * Because the paste logic (onPaste / renamePastedImage / mimeToExt) lives
 * entirely inside <script setup> and is not exported, we drive it through
 * the real DOM event path that Vue wires up via @paste="onPaste".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import TopicChat from '../TopicChat.vue'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal router that satisfies useRoute() inside TopicChat.
 * The component reads route.params.wsId and route.params.topicId on setup.
 */
function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/workspaces/:wsId/topics/:topicId',
        component: TopicChat,
      },
    ],
  })
  return router
}

/**
 * Construct a synthetic ClipboardEvent whose clipboardData.items list is
 * populated from the provided array of item descriptors.
 *
 * Each descriptor: { kind, type, file }
 *   - kind: 'file' | 'string'
 *   - type: MIME string
 *   - file: File | null  (returned by getAsFile(); ignored when kind !== 'file')
 */
function makePasteEvent(itemDescriptors) {
  const items = itemDescriptors.map(({ kind, type, file }) => ({
    kind,
    type,
    getAsFile: () => file ?? null,
  }))

  // DataTransferItemList-like: iterable + length
  const itemList = Object.assign(items, { length: items.length })

  const clipboardData = { items: itemList }
  const event = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'clipboardData', { value: clipboardData })
  return event
}

/**
 * Mount TopicChat at the route /workspaces/ws1/topics/t1 and wait for the
 * router to be ready. Stubs MarkdownMessage to avoid needing the full
 * render chain.  Mocks fetch to return empty data so onMounted doesn't throw.
 */
async function mountComponent() {
  const router = makeRouter()
  await router.push('/workspaces/ws1/topics/t1')
  await router.isReady()

  // Suppress network calls made in onMounted (load + connectWs).
  // The component fetches topic (returns an object) and then messages (returns an array).
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ subject: 'Test', archived_at: null }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    })
    .mockResolvedValue({
      ok: true,
      json: async () => [],
    })
  vi.stubGlobal('fetch', fetchMock)

  // WebSocket mock must be a class (constructor) because the code does `new WebSocket(...)`.
  class WsMock {
    constructor() {
      this.onmessage = null
      this.onclose = null
    }
    close() {}
  }
  vi.stubGlobal('WebSocket', WsMock)

  const wrapper = mount(TopicChat, {
    global: {
      plugins: [router],
      stubs: { MarkdownMessage: true },
    },
  })

  // Wait for onMounted async load to settle
  await vi.waitFor(() => wrapper.find('textarea').exists(), { timeout: 1000 })

  return wrapper
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('TopicChat paste handler', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  // -------------------------------------------------------------------------
  // TC-1: Single image paste
  // -------------------------------------------------------------------------
  it('adds a single pasted image to selectedFiles with a timestamped filename', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const fakeFile = new File([new Uint8Array([1, 2, 3])], 'image.png', { type: 'image/png' })
    const event = makePasteEvent([{ kind: 'file', type: 'image/png', file: fakeFile }])

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    // Find the file-chips rendered in the DOM — each chip displays the filename
    const chips = wrapper.findAll('.file-chip')
    expect(chips).toHaveLength(1)

    const chipText = chips[0].text()
    // Filename must start with 'pasted-image-' and end with '.png'
    expect(chipText).toMatch(/pasted-image-.+\.png/)
  })

  // -------------------------------------------------------------------------
  // TC-2: Multiple images in one paste event
  // -------------------------------------------------------------------------
  it('adds all images when multiple image items are in the clipboard', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const file1 = new File([new Uint8Array([1])], 'a.png', { type: 'image/png' })
    const file2 = new File([new Uint8Array([2])], 'b.jpeg', { type: 'image/jpeg' })

    const event = makePasteEvent([
      { kind: 'file', type: 'image/png', file: file1 },
      { kind: 'file', type: 'image/jpeg', file: file2 },
    ])

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    const chips = wrapper.findAll('.file-chip')
    expect(chips).toHaveLength(2)
  })

  // -------------------------------------------------------------------------
  // TC-3: Text-only paste — no interference
  // -------------------------------------------------------------------------
  it('does not add files and does not call preventDefault for a text-only paste', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const event = makePasteEvent([{ kind: 'string', type: 'text/plain', file: null }])
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.file-chip')).toHaveLength(0)
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // TC-4: Non-image file in clipboard (e.g. PDF)
  // -------------------------------------------------------------------------
  it('does not add a non-image file kind to selectedFiles', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const pdfFile = new File([new Uint8Array([0])], 'doc.pdf', { type: 'application/pdf' })
    const event = makePasteEvent([{ kind: 'file', type: 'application/pdf', file: pdfFile }])
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.file-chip')).toHaveLength(0)
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // TC-5: Filename format — no colons, matches pattern
  // -------------------------------------------------------------------------
  it('generates a filename without colons that matches pasted-image-*.{ext}', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const fakeFile = new File([new Uint8Array([7])], 'screenshot.png', { type: 'image/png' })
    const event = makePasteEvent([{ kind: 'file', type: 'image/png', file: fakeFile }])

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    const chips = wrapper.findAll('.file-chip')
    expect(chips).toHaveLength(1)

    const name = chips[0].text().replace('×', '').trim()
    expect(name).not.toContain(':')
    // Pattern: pasted-image-<ISO-timestamp-with-dashes>.<ext>
    expect(name).toMatch(/^pasted-image-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z\.png$/)
  })

  // -------------------------------------------------------------------------
  // TC-6: getAsFile() returns null — gracefully skipped, no crash
  // -------------------------------------------------------------------------
  it('skips an image item where getAsFile() returns null without crashing', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    // kind=file, type=image/png, but getAsFile returns null
    const event = makePasteEvent([{ kind: 'file', type: 'image/png', file: null }])
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')

    expect(() => textarea.dispatchEvent(event)).not.toThrow()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.file-chip')).toHaveLength(0)
    expect(preventDefaultSpy).not.toHaveBeenCalled()
  })

  // -------------------------------------------------------------------------
  // TC-7: preventDefault is called when at least one image is captured
  // -------------------------------------------------------------------------
  it('calls preventDefault when an image item is successfully captured', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const fakeFile = new File([new Uint8Array([1])], 'img.png', { type: 'image/png' })
    const event = makePasteEvent([{ kind: 'file', type: 'image/png', file: fakeFile }])
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    expect(preventDefaultSpy).toHaveBeenCalledOnce()
  })

  // -------------------------------------------------------------------------
  // TC-8: mimeToExt mapping — various MIME types produce correct extensions
  // -------------------------------------------------------------------------
  it('assigns the correct file extension for known image MIME types', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const cases = [
      { type: 'image/png', ext: 'png' },
      { type: 'image/jpeg', ext: 'jpg' },
      { type: 'image/gif', ext: 'gif' },
      { type: 'image/webp', ext: 'webp' },
      { type: 'image/bmp', ext: 'bmp' },
      { type: 'image/svg+xml', ext: 'svg' },
    ]

    for (const { type, ext } of cases) {
      const fakeFile = new File([new Uint8Array([0])], `img.${ext}`, { type })
      const event = makePasteEvent([{ kind: 'file', type, file: fakeFile }])

      textarea.dispatchEvent(event)
      await wrapper.vm.$nextTick()

      const chips = wrapper.findAll('.file-chip')
      const lastName = chips[chips.length - 1].text().replace('×', '').trim()
      expect(lastName).toMatch(new RegExp(`\\.${ext}$`))
    }
  })

  // -------------------------------------------------------------------------
  // TC-8b: mimeToExt fallback — unknown and vendor MIME types
  // -------------------------------------------------------------------------
  it('maps image/tiff to tiff and vendor MIME types with dots to png fallback', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    // image/tiff — matches regex, produces 'tiff'
    const tiffFile = new File([new Uint8Array([0])], 'img.tiff', { type: 'image/tiff' })
    const tiffEvent = makePasteEvent([{ kind: 'file', type: 'image/tiff', file: tiffFile }])
    textarea.dispatchEvent(tiffEvent)
    await wrapper.vm.$nextTick()
    let chips = wrapper.findAll('.file-chip')
    expect(chips[chips.length - 1].text().replace('×', '').trim()).toMatch(/\.tiff$/)

    // image/vnd.microsoft.icon — dot in subtype, regex rejects it, falls back to 'png'
    const icoFile = new File([new Uint8Array([0])], 'img.ico', { type: 'image/vnd.microsoft.icon' })
    const icoEvent = makePasteEvent([{ kind: 'file', type: 'image/vnd.microsoft.icon', file: icoFile }])
    textarea.dispatchEvent(icoEvent)
    await wrapper.vm.$nextTick()
    chips = wrapper.findAll('.file-chip')
    expect(chips[chips.length - 1].text().replace('×', '').trim()).toMatch(/\.png$/)

    // image/ with empty subtype — regex rejects it, falls back to 'png'
    const emptyFile = new File([new Uint8Array([0])], 'img', { type: 'image/' })
    const emptyEvent = makePasteEvent([{ kind: 'file', type: 'image/', file: emptyFile }])
    textarea.dispatchEvent(emptyEvent)
    await wrapper.vm.$nextTick()
    chips = wrapper.findAll('.file-chip')
    expect(chips[chips.length - 1].text().replace('×', '').trim()).toMatch(/\.png$/)
  })

  // -------------------------------------------------------------------------
  // TC-9: Mixed clipboard — image + text — image captured, text not blocked
  // -------------------------------------------------------------------------
  it('captures images and ignores text items in a mixed clipboard event', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const fakeFile = new File([new Uint8Array([1])], 'screenshot.png', { type: 'image/png' })
    const event = makePasteEvent([
      { kind: 'string', type: 'text/plain', file: null },
      { kind: 'file', type: 'image/png', file: fakeFile },
    ])

    textarea.dispatchEvent(event)
    await wrapper.vm.$nextTick()

    // One image was captured
    expect(wrapper.findAll('.file-chip')).toHaveLength(1)
  })

  // -------------------------------------------------------------------------
  // TC-10: Empty clipboard items list — no crash
  // -------------------------------------------------------------------------
  it('handles an event with no clipboard items without crashing', async () => {
    const wrapper = await mountComponent()
    const textarea = wrapper.find('textarea').element

    const event = makePasteEvent([])
    expect(() => textarea.dispatchEvent(event)).not.toThrow()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.file-chip')).toHaveLength(0)
  })
})
