# Test Plan: Rich File Attachment Support (v3.5)

*Feature ADR:* [ADR-0001 — Rich file attachment support](../decisions/0001-rich-file-attachment-support.md)
*Scope:* `src/master/` only. `src/bot/` is not in scope for v3.5.
*Framework:* pytest with `unittest.mock` / `pytest-mock`. No real Podman, Slack, or Discord API calls.

---

## 1. Inbound conversion — `src/master/file_converter.py`

### Happy-path conversion

| ID | Input | Expected output |
|----|-------|----------------|
| FC-01 | `.docx` file with plain text content | Returns extracted text string |
| FC-02 | `.xlsx` file with data cells | Returns CSV-like text representation |
| FC-03 | `.csv` file with rows | Returns raw text content |
| FC-04 | `.pdf` file with extractable text | Returns extracted text string |

### Unsupported type fallback

| ID | Input | Expected output |
|----|-------|----------------|
| FC-05 | `.zip` file (unsupported type) | Returns `[attachment: filename.zip skipped — unsupported type]` notice |
| FC-06 | Binary file with no extension | Returns skip notice containing the filename |
| FC-07 | Converter library raises an unexpected exception | Returns skip notice rather than propagating the exception |

### Complex docx format hint

| ID | Condition | Expected output |
|----|-----------|----------------|
| FC-08 | `.docx` file where extracted text is very short relative to file size (complex formatting detected) | Appended hint: *"For best results with text + images, send a `.md` or `.txt` file and attach images separately."* |
| FC-09 | `.docx` file with normal text-to-size ratio | No format hint appended |

### Token budget — inline vs staged-pointer

| ID | Condition | Expected outcome |
|----|-----------|-----------------|
| FC-10 | Extracted text character count is below the token budget threshold | Text is returned for direct inline injection into the prompt |
| FC-11 | Extracted text character count exceeds the token budget threshold | Caller receives a staged-pointer notice referencing the workspace file path |

*Pass criteria:* All assertions pass; no real filesystem I/O required (use `tmp_path` or `BytesIO` where needed).

---

## 2. Inbound platform handlers

### Slack (non-image file inbound)

| ID | Scenario | Expected behaviour |
|----|----------|-------------------|
| SL-01 | Slack event contains a non-image file attachment | File is downloaded using `Authorization: Bearer <token>` header |
| SL-02 | Downloaded file is passed to `file_converter` | Converted text is injected into the prompt sent to the agent |
| SL-03 | Slack event contains an image attachment (regression) | Existing image staging path (`_stage_images`) is still invoked; no regression |
| SL-04 | `file_paths` in `DispatchResult` are uploaded via `files_upload_v2` | One `files_upload_v2` call per path in `result.file_paths` |
| SL-05 | `result.file_paths` is empty | No `files_upload_v2` call is made |

### Discord (inbound)

| ID | Scenario | Expected behaviour |
|----|----------|-------------------|
| DC-01 | Discord attachment with type not in the legacy allowlist (e.g. `.docx`) | Attachment is accepted and processed; no `type not accepted` rejection |
| DC-02 | Discord attachment of exactly 20 MB | Accepted (new size limit) |
| DC-03 | Discord attachment of 20 MB + 1 byte | Rejected with an appropriate user-facing message |
| DC-04 | Discord CDN URL (expires ~1 hr) | Bytes are read immediately on receipt; the URL is not passed to the agent |
| DC-05 | Discord CDN read fails (network error) | Graceful error logged; prompt continues without the attachment |

*Pass criteria:* Platform clients are fully mocked; no real HTTP calls.

---

## 3. DispatchResult

| ID | Scenario | Expected behaviour |
|----|----------|--------------------|
| DR-01 | `DispatchResult` created with only `text` | `file_paths` defaults to `[]` |
| DR-02 | `DispatchResult` created with `text` and two `Path` values | `file_paths` contains exactly those two paths |
| DR-03 | Codex adapter (`PodmanExecDispatcher`) returns a result | `file_paths` is always `[]` |
| DR-04 | `ClaudeCodeDispatcher.send_prompt()` returns a `DispatchResult` | `text` contains the parsed agent reply; `file_paths` contains paths from the JSON envelope |
| DR-05 | Prompt to codex adapter contained file attachment markers | Response text is prepended with the "not supported" notice: *"File attachments in replies are not supported for this agent type…"* |
| DR-06 | `route_prompt()` on `ChannelRouter` always returns `DispatchResult` (not `str`) | Return type is `DispatchResult` on all adapter paths |

*Pass criteria:* Dataclass field defaults verified; codex notice injection verified by string match.

---

## 4. Outbound — Discord compression pipeline

| ID | Condition | Expected behaviour |
|----|-----------|-------------------|
| DP-01 | Guild has Nitro tier 2+ (premium_tier >= 2) | File is attached directly; no compression step run |
| DP-02 | File size ≤ 25 MB, no Nitro | File attached directly without compression |
| DP-03 | File size > 25 MB, no Nitro | ZIP recompression attempted |
| DP-04 | ZIP recompressed file is ≤ 25 MB | Compressed file is attached |
| DP-05 | ZIP recompressed file is still > 25 MB | Falls through to next pipeline step |
| DP-06 | `libreoffice` binary is absent on PATH | LibreOffice conversion step is skipped; pipeline falls to hard-failure notice |
| DP-07 | All compression steps exhausted (hard failure) | Correct failure message posted to channel; no exception raised to caller |
| DP-08 | Hard-failure message content | Contains the workspace file path so the user knows where the file lives |

*Pass criteria:* `shutil.which` and `subprocess.run` are mocked; no real processes spawned.

---

## 5. Outbound — Slack

| ID | Scenario | Expected behaviour |
|----|----------|-------------------|
| SK-01 | `result.file_paths` contains two paths | `files_upload_v2` is called twice, once per path |
| SK-02 | `result.file_paths` is empty | `files_upload_v2` is never called |
| SK-03 | `files_upload_v2` raises an exception | Error is logged; no exception propagates to the caller |

*Pass criteria:* Slack client is mocked; upload call count is asserted.

---

## 6. Interface contracts (priority)

| ID | Contract | Test approach |
|----|----------|---------------|
| IC-01 | `route_prompt()` return type is `DispatchResult` on all adapters | Monkeypatch `send_prompt` on each adapter class to return `DispatchResult`; assert return type |
| IC-02 | `podman cp` called correctly for each output file path | Monkeypatch `subprocess.run`; assert `podman cp <tmp> <container>:<path>` for each path in `file_paths` |
| IC-03 | Slack handler does not crash when `file_paths` is empty | Call the upload helper with an empty list; assert no exception and no upload call |
| IC-04 | Discord handler does not crash when `file_paths` is empty | Call the send-file helper with an empty list; assert no exception |
| IC-05 | `AgentDispatcher` Protocol signature includes `DispatchResult` return type | Structural check: `PodmanExecDispatcher` and `ClaudeCodeDispatcher` satisfy the updated Protocol |

*Pass criteria:* No `AttributeError` or `TypeError` raised on any adapter path for empty `file_paths`.

---

## Non-functional requirements

| Requirement | Acceptance criterion |
|-------------|---------------------|
| Context overflow prevention | A file exceeding the token budget produces a staged-pointer notice rather than an oversized prompt |
| No silent failures on interface boundaries | Every adapter path for `send_prompt` / `route_prompt` must either return `DispatchResult` or raise `RouteError`; no bare `str` returns |
| Platform isolation | Discord compression logic must not touch Slack code paths and vice versa (verified by import assertions in tests) |

---

## UAT Checklist (manual testing)

The following steps must be performed by a human tester against a running instance before merging to `master`.

### Slack inbound — document upload

- [ ] Upload a `.docx` file to an agent-mapped Slack channel; verify the agent receives the extracted text in its prompt.
- [ ] Upload an `.xlsx` file; verify the agent receives a text/CSV representation.
- [ ] Upload a `.pdf` file; verify the agent receives extracted text.
- [ ] Upload a `.zip` file; verify a `[attachment: … skipped]` notice appears in the prompt (check agent logs) and the agent is not confused.
- [ ] Upload a large docx (> token budget); verify the agent receives a staged-pointer notice rather than the full text.
- [ ] Upload an image alongside text; verify both the image staging path and the text conversion path fire (check logs for both events).
- [ ] Confirm Slack image staging still works as before (regression): upload an image-only message and verify the agent sees the staged image path.

### Discord inbound — document upload

- [ ] Upload a `.docx` file to an agent-mapped Discord channel; verify the agent receives the extracted text.
- [ ] Upload a file type that was previously blocked (e.g. `.xlsx`); verify it is now accepted and converted.
- [ ] Upload a file just under 20 MB; verify it is accepted.
- [ ] Upload a file just over 20 MB; verify it is rejected with a clear user-facing message.
- [ ] Confirm CDN URL is downloaded immediately (verify by checking that the agent receives file content, not a URL).

### Outbound — agent returns a file (claude-code adapter only)

- [ ] Instruct the agent to produce a file output; verify the master posts the file as a Slack attachment.
- [ ] Instruct the agent to produce a file output in Discord; verify the file is posted (or compressed if over 25 MB).
- [ ] Trigger the Discord compression pipeline by producing a file over 25 MB; verify ZIP recompression is attempted.
- [ ] If LibreOffice is absent in the container, verify the hard-failure notice is posted with the workspace path.

### Codex adapter — "not supported" notice

- [ ] On a codex-adapter agent, instruct it to produce a file; verify the response includes the "not supported" notice.
- [ ] Verify the agent still responds to the prompt content (notice is prepended, not a replacement).

### Regression

- [ ] Confirm plain text prompt/reply flow on both Slack and Discord is unaffected.
- [ ] Confirm `/master-agent-*` slash commands still work on the admin channel.
- [ ] Confirm usage metrics (`/master-agent-usage`) are still recorded correctly.
