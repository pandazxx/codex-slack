# Test Plan: Clipboard Paste Image Support

- **Feature design:** [docs/design/paste-image-support.md](../design/paste-image-support.md)
- **Date:** 2026-05-05
- **Components under test:** `frontend/src/views/TopicChat.vue` — `onPaste`, `renamePastedImage`, `mimeToExt`
- **Test file:** `frontend/src/views/__tests__/TopicChat.paste.test.js`

---

## 1. Scope

### In scope

- The `@paste` event handler (`onPaste`) attached to the chat textarea in `TopicChat.vue`.
- Helper functions `renamePastedImage` and `mimeToExt` as exercised through the handler.
- Filename generation: format, timestamp encoding, extension derivation from MIME type.
- `selectedFiles` ref population: correct number of entries, correct File objects.
- `preventDefault()` call semantics: called only when at least one image was captured.
- Defensive handling of null returns from `getAsFile()`.
- Non-image clipboard content (text-only, PDF) being left untouched.
- Multiple images pasted in a single event.
- End-to-end visibility of pasted images in the chip strip and their inclusion in a send.

### Out of scope

- Backend attachment upload and download (tested in `tests/master/test_attachments.py`).
- Message send with files (tested in `tests/master/test_messages.py`).
- File picker (`<input type="file">`) path — pre-existing feature.
- Image thumbnail preview — explicitly deferred per design doc (Non-goals).
- Drag-and-drop — separate feature.
- Client-side image size limits — deferred per design doc (Open Questions).
- Cross-browser clipboard API compatibility (Chrome, Firefox, Safari) — UAT only.
- Accessibility / aria-live announcement for captured images — flagged as open question in design.

---

## 2. Test Environment Prerequisites

### Automated tests

- Node.js >= 18, npm available.
- `frontend/` dependencies installed (`npm install`), including `vitest`, `@vue/test-utils`, and `jsdom`.
- Run with: `cd frontend && npm test -- --run`

### UAT (human)

- A running deployment reachable at the UI URL (local `docker compose up` or testbed).
- A browser with clipboard access (Chrome, Firefox, or Safari).
- A screenshot utility or an image on the system clipboard (e.g. Cmd+Shift+4 on macOS, Win+Shift+S on Windows, or `xclip`/`gnome-screenshot` on Linux).

---

## 3. Test Cases

### TC-1: Single image paste — automated

**What:** Paste a single `image/png` clipboard item into the textarea.

**How (automated):** Dispatch a synthetic `ClipboardEvent` containing one item with `kind='file'`, `type='image/png'`, and a non-null `File`. Assert that `selectedFiles` gains exactly one entry (visible as one chip in the DOM).

**Expected:** One file chip appears. The chip filename matches `pasted-image-*.png`.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-1.

---

### TC-2: Multiple images paste — automated

**What:** Paste two image items in a single clipboard event.

**How (automated):** Dispatch a synthetic event with one `image/png` and one `image/jpeg` item. Assert two chips appear.

**Expected:** `selectedFiles` has 2 entries. Both chips are visible.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-2.

---

### TC-3: Text-only paste — no interference — automated

**What:** Paste plain text; the handler must not intercept it.

**How (automated):** Dispatch a synthetic event with one item of `kind='string'`, `type='text/plain'`. Assert no chips appear and `preventDefault` was not called.

**Expected:** `selectedFiles` unchanged (empty). `preventDefault` not called, allowing normal browser text-insert behavior.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-3.

---

### TC-4: Non-image file in clipboard — automated

**What:** Paste an `application/pdf` file; it must not be added to `selectedFiles`.

**How (automated):** Dispatch a synthetic event with `kind='file'`, `type='application/pdf'`. Assert no chips appear and `preventDefault` was not called.

**Expected:** `selectedFiles` unchanged. `preventDefault` not called.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-4.

---

### TC-5: Filename format — no colons, timestamped — automated

**What:** Verify the generated filename is safe for all filesystems and follows the documented pattern.

**How (automated):** Paste one `image/png` item. Read the chip text. Assert the name matches the regex `^pasted-image-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z\.png$` and contains no `:` characters.

**Expected:** Filename like `pasted-image-2026-05-05T08-30-00-000Z.png` — colons replaced with dashes throughout.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-5.

---

### TC-6: `getAsFile()` returns null — gracefully skipped — automated

**What:** Defensive behavior when the clipboard item exists but `getAsFile()` returns null.

**How (automated):** Dispatch a synthetic event with `kind='file'`, `type='image/png'`, but `getAsFile` returning `null`. Assert no crash, no chips, and `preventDefault` not called (no images were captured).

**Expected:** Handler exits cleanly. `selectedFiles` unchanged.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-6.

---

### TC-7: `preventDefault` called on successful image capture — automated

**What:** Confirm `preventDefault` is called exactly once when an image is captured, suppressing the browser's default binary-paste behavior.

**How (automated):** Spy on `event.preventDefault`. Paste one valid image. Assert the spy was called exactly once.

**Expected:** `preventDefault` called once.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-7.

---

### TC-8: MIME-to-extension mapping — automated

**What:** Verify `mimeToExt` produces the correct extension for each known MIME type.

**How (automated):** For each of `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/bmp`, `image/svg+xml` — paste one item and assert the chip filename ends with the expected extension (`png`, `jpg`, `gif`, `webp`, `bmp`, `svg`).

**Expected:**

| MIME type        | Extension |
|------------------|-----------|
| `image/png`      | `png`     |
| `image/jpeg`     | `jpg`     |
| `image/gif`      | `gif`     |
| `image/webp`     | `webp`    |
| `image/bmp`      | `bmp`     |
| `image/svg+xml`  | `svg`     |

**Status:** automated — covered in `TopicChat.paste.test.js` TC-8.

---

### TC-9: Mixed clipboard (image + text string) — automated

**What:** When the clipboard contains both a text item and an image item, the image is captured and the text item is silently ignored.

**How (automated):** Dispatch an event with one `kind='string'` text item and one `kind='file'` image item. Assert exactly one chip appears.

**Expected:** One chip for the image. Text item ignored.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-9.

---

### TC-10: Empty clipboard items list — automated

**What:** Handler is robust when `clipboardData.items` is empty.

**How (automated):** Dispatch a synthetic event with zero items. Assert no crash and no chips.

**Expected:** Handler returns early with no side effects.

**Status:** automated — covered in `TopicChat.paste.test.js` TC-10.

---

### TC-11: Real browser paste — Chrome — needs-human

**What:** Verify the feature works end-to-end in Chrome with the real clipboard API.

**Steps:**
1. Open the app in Chrome and navigate to any topic chat.
2. Take a screenshot (Cmd+Shift+4 on macOS / Win+Shift+S on Windows / `gnome-screenshot -c` on Linux) so an image is on the system clipboard.
3. Click inside the chat textarea.
4. Press Cmd+V (macOS) or Ctrl+V (Windows/Linux).
5. Observe the file chip strip above the textarea.
6. Type any message text and click Send.
7. Observe the sent message in the chat.

**Expected:** A chip labeled `pasted-image-<timestamp>.png` (or relevant extension) appears in the chip strip after paste. After sending, the message bubble contains the attached image rendered inline.

**Status:** needs-human.

---

### TC-12: Real browser paste — Firefox — needs-human

**What:** Same as TC-11 in Firefox.

**Steps:** Same as TC-11, using Firefox.

**Expected:** Same as TC-11.

**Status:** needs-human.

---

### TC-13: Paste multiple images simultaneously — needs-human

**What:** Verify multiple images can be pasted in one event (e.g. copying multiple items from a file manager or a design tool that places multiple images on the clipboard).

**Steps:**
1. Find a tool or workflow that places multiple images on the clipboard simultaneously (e.g. copy two files in Finder on macOS, or use a browser extension to copy multiple images).
2. Paste into the textarea.
3. Observe the chip strip.

**Expected:** Multiple chips appear, one per pasted image. All are included in the next send.

**Note:** Most OS/browser combinations only surface one image per Ctrl+V even when multiple files are selected. This test is best effort — if only one image lands, that is an OS-level constraint, not a bug.

**Status:** needs-human.

---

### TC-14: Plain text paste is unaffected — needs-human

**What:** Confirm that pasting plain text (e.g. from another document) still inserts text into the textarea normally and does not add any chips.

**Steps:**
1. Copy any text to clipboard (e.g. Cmd+C on a paragraph of text in a browser).
2. Click the chat textarea.
3. Press Cmd+V / Ctrl+V.

**Expected:** Text is inserted into the textarea at the cursor position. No file chip appears. No console errors.

**Status:** needs-human.

---

### TC-15: Textarea disabled during send — needs-human

**What:** Paste should not cause crashes or state corruption when the textarea is disabled (i.e. a send is in flight).

**Steps:**
1. Paste an image into the textarea (chip appears).
2. Type a message and begin sending (click Send and immediately try to paste again while the spinner is active, if timing allows).

**Expected:** Either the paste during send is ignored or it is buffered; no crash; no duplicate chips in a broken state.

**Note:** This is a timing-dependent test; best-effort manual verification.

**Status:** needs-human.

---

## 4. Non-Functional Requirements

| Requirement | Verification method |
|-------------|---------------------|
| No synchronous blocking: handler completes in < 5 ms for a typical screenshot | Browser DevTools Performance panel (needs-human) |
| Filename safe on Windows (no `:` or `*` characters) | Covered by TC-5 (automated regex assertion) |
| No memory leaks from repeated paste operations | Browser DevTools Memory snapshot after 20 paste cycles (needs-human, low priority) |

---

## 5. Pass / Fail Criteria

- All 10 automated test cases must be green (`npm test -- --run` exits 0) before the PR is merged.
- TC-11 and TC-12 (real browser, single image) are blocking UAT sign-off items.
- TC-13, TC-14, TC-15 are non-blocking but should be attempted and reported.
- No regressions in pre-existing test suites (`tests/master/test_attachments.py`, `tests/master/test_messages.py`).
