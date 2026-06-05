# fastrtc emit-queue starvation - upstream contribution kit

Everything needed to report + fix the fastrtc websocket emit-queue starvation bug upstream.
Copy-paste sources below. Our local fix lives in
`src/realtime_phone_agents/observability/emit_instrument.py` (monkeypatch).

- **Repo:** https://github.com/gradio-app/fastrtc
- **Related open issue:** https://github.com/gradio-app/fastrtc/issues/203
  ("AsyncStreamHandler breaks when no `await` is used in `emit()`") - same starvation
  mechanism, reported from the user's `emit()` side with the folk-remedy "add
  `await asyncio.sleep(0.01)` to emit()". No maintainer response, no root-cause analysis.
- **Affected code:** `backend/fastrtc/websocket.py`, `WebSocketHandler._emit_to_queue`
  (lines ~147-160 on `main`). Unchanged in latest release `0.0.34`; we hit it on `0.0.33`.
- **Status check (Jun 5 2026):** bug still present on `main`/`0.0.34`; `0.0.34` changelog is
  unrelated (OpenAPI tags, logging). Not fixed upstream.

Plan: post the comment below on issue #203, then open a PR with the description + diff and
link it from the comment.

---

## 1. Comment to post on issue #203

> I dug into the root cause of this from the telephony/websocket side and I think it's the
> same bug. Posting the analysis in case it helps, plus a proposed library-side fix.
>
> **Symptom (our case):** on inbound phone calls, the caller heard up to ~18s of dead air
> before the agent's (already-generated, cached) greeting, intermittently.
>
> **Root cause:** the producer `WebSocketHandler._emit_to_queue` is a tight loop:
>
> ```python
> while not self.quit.is_set():
>     output = await self.stream_handler.emit()   # (or run_sync(emit_with_context))
>     self.queue.put_nowait(output)
> ```
>
> When a stream handler is idle, `emit()` returns `None` **instantly** on every call. Because
> the queue is unbounded and there's no backpressure or yield on the idle path, this spins at
> thousands of iterations/sec enqueuing `None`. That floods the queue (we observed depth
> ballooning to ~164k items) and starves the consumer `_emit_loop` from being scheduled to
> actually send the frame that's already sitting at the head of the queue.
>
> This also explains why the `await asyncio.sleep(0.01)`-in-`emit()` workaround in this issue
> "fixes" it: that sleep is the only thing yielding the event loop back to the consumer. The
> requirement is currently implicit - users have to discover it the hard way.
>
> **Why it's safe to fix in the library:** `_emit_loop` already skips `None` outputs, so
> enqueuing `None` was never doing anything except creating work and contention. Backing off
> on the idle path removes wasted work without changing any downstream behavior.
>
> **Proposed fix** (PR linked below): on the idle path (`output is None`), back off briefly
> and skip the enqueue instead of spinning:
>
> ```python
> if output is None:
>     await asyncio.sleep(0.02)
>     continue
> self.queue.put_nowait(output)
> ```
>
> **Validation:** in-call on the real phone path, first-frame send time dropped from ~18.6s
> to ~0.1-0.4s with queue depth ~0 on every call. (We're on `0.0.33`; the same code is
> unchanged on `main`/`0.0.34`.)
>
> Open to alternatives if the maintainers prefer a different shape - e.g. bounding the queue,
> or documenting the `emit()` await requirement explicitly rather than handling it in the
> loop. Happy to adjust the PR.

---

## 2. PR description

**Title:** Fix websocket emit-queue starvation when `emit()` returns `None`

**Body:**

> ### Problem
> `WebSocketHandler._emit_to_queue` enqueues whatever `emit()` returns with no guard:
>
> ```python
> while not self.quit.is_set():
>     output = await self.stream_handler.emit()
>     self.queue.put_nowait(output)
> ```
>
> When a handler is idle, `emit()` returns `None` instantly every call. With an unbounded
> queue and no yield on the idle path, this spins thousands of times/sec enqueuing `None`,
> floods the queue (observed ~164k depth), and starves the consumer `_emit_loop` so it isn't
> scheduled to send the frame already at the head of the queue.
>
> Real-world impact (telephony): up to ~18s of intermittent dead air before an
> already-generated greeting reached the caller. Related: #203, where the same starvation is
> worked around by adding `await asyncio.sleep(0.01)` inside the user's `emit()`.
>
> ### Fix
> On the idle path (`output is None`), back off 20ms and skip the enqueue:
>
> ```python
> if output is None:
>     await asyncio.sleep(0.02)
>     continue
> self.queue.put_nowait(output)
> ```
>
> ### Why this is safe
> `_emit_loop` already skips `None` outputs, so enqueuing `None` never produced a frame - it
> only created work and event-loop contention. This removes wasted work and yields the loop to
> the consumer on idle; no downstream behavior changes.
>
> ### Validation
> On the live phone path, first-frame send dropped from ~18.6s to ~0.1-0.4s with queue depth
> ~0 on every call. Tested on `0.0.33`; the affected code is identical on `main`.
>
> ### Notes / alternatives
> - 20ms is a conservative idle backoff (negligible added latency vs the starvation it
>   prevents). Open to tuning.
> - Alternatives considered: bounding the queue, or documenting the `emit()` await requirement
>   instead of handling it here. Went with the loop-side guard because it's minimal and fixes
>   the existing #203 workaround at the source. Happy to reshape.

---

## 3. The diff

Against `backend/fastrtc/websocket.py` (`_emit_to_queue`, ~line 147 on `main`):

```diff
 async def _emit_to_queue(self):
     try:
         while not self.quit.is_set():
             if isinstance(self.stream_handler, AsyncStreamHandler):
                 output = await self.stream_handler.emit()
             else:
                 output = await run_sync(self.emit_with_context)
+            if output is None:
+                # Idle: nothing to send. Back off so the consumer _emit_loop gets
+                # scheduled, instead of spinning put_nowait(None) and flooding the queue.
+                await asyncio.sleep(0.02)
+                continue
             self.queue.put_nowait(output)
     except asyncio.CancelledError:
         logger.debug("Emit loop cancelled")
     except Exception as e:
         import traceback

         traceback.print_exc()
         logger.debug("Error in emit loop: %s", e)
```

`asyncio` is already imported in `websocket.py`, so no new import is needed.

---

## 4. Pre-submit checklist

- [ ] Fork `gradio-app/fastrtc`, branch from `main`.
- [ ] Re-confirm `_emit_to_queue` on `main` still matches the "before" above (it did Jun 5 2026).
- [ ] Apply the diff; run the repo's lint/tests if present (check `CONTRIBUTING`/`Makefile`).
- [ ] Sanity-check that `_emit_loop` still skips `None` on `main` (it did - this is why the
      fix is behavior-preserving).
- [ ] Open PR, paste section 2.
- [ ] Post section 1 on issue #203, link the PR.
- [ ] Optional: mention our use case (FastRTC + Twilio telephony) for context.
