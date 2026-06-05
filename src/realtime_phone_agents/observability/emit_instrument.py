"""Patch for a fastrtc 0.0.33 websocket emit-queue starvation bug.

Symptom: on inbound phone calls, the caller heard up to ~18s of dead air before Leo's
greeting, intermittently. Root cause (convicted Jun 5 2026 via trace timing on the live
phone path — NOT ngrok/Twilio, which were exonerated): after the greeting generator
exhausts, ``ReplyOnPause.emit()`` returns ``None`` instantly on every call. fastrtc's
producer ``WebSocketHandler._emit_to_queue`` is a tight ``while: queue.put_nowait(await emit())``
loop with an unbounded queue and no backpressure, so it spins ~9000 None/sec and starves
the consumer ``_emit_loop`` from being scheduled to actually send the greeting that is
already sitting at the head of the queue (observed queue depth ballooned to 164k items).

Fix: when ``emit()`` returns ``None`` (idle, nothing to send), back off 20ms and skip the
enqueue instead of flooding the queue. ``_emit_loop`` already skips ``None`` outputs, so
nothing downstream changes; the backoff yields the event loop to the consumer. Validated
in-call: first-frame send dropped from ~18.6s to ~0.1-0.4s with queue depth ~0 on every call.

Applied as a monkeypatch (fastrtc is a third-party dependency); ``install()`` is called once
from ``api/main.py``. Worth reporting upstream to fastrtc.
"""

import asyncio

from anyio.to_thread import run_sync
from loguru import logger

from fastrtc.tracks import AsyncStreamHandler
from fastrtc.websocket import WebSocketHandler


async def _patched_emit_to_queue(self):
    """fastrtc 0.0.33 ``websocket.py:203-217`` with an idle-backoff guard added.

    Only change vs upstream: when ``emit()`` returns ``None`` there is nothing to send, so
    back off 20ms and skip the enqueue rather than ``put_nowait(None)`` in a tight spin.
    """
    try:
        while not self.quit.is_set():
            if isinstance(self.stream_handler, AsyncStreamHandler):
                output = await self.stream_handler.emit()
            else:
                output = await run_sync(self.emit_with_context)
            if output is None:
                # Idle: nothing to send — back off so _emit_loop gets scheduled.
                await asyncio.sleep(0.02)
                continue
            self.queue.put_nowait(output)
    except asyncio.CancelledError:
        logger.debug("Emit-to-queue loop cancelled")
    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.debug("Error in emit-to-queue loop: %s", e)


def install() -> None:
    """Apply the fastrtc emit-queue starvation patch (call once at startup)."""
    WebSocketHandler._emit_to_queue = _patched_emit_to_queue
    logger.info("Applied fastrtc emit-queue starvation patch (_emit_to_queue idle backoff)")
