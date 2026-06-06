import asyncio

from fastrtc import Stream
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from loguru import logger
from typing import Any, Callable, Literal
from gradio.components.base import Component
from fastrtc.tracks import HandlerType
from fastrtc.utils import RTCConfigurationCallable


MAX_CALL_DURATION_SECONDS = 300  # 5 minutes — hard cutoff at Twilio level


class VoiceAgentStream(Stream):
    _caller_phone: str | None = None
    _call_sid: str | None = None
    _on_caller_phone: Callable[[str], None] | None = None
    _background_tasks: set[asyncio.Task]

    def hang_up(self) -> None:
        """Terminate the active call via Twilio REST API."""
        from twilio.rest import Client
        from realtime_phone_agents.config import settings

        if not self._call_sid:
            logger.warning("hang_up called but no active CallSid")
            return
        try:
            client = Client(settings.twilio.account_sid, settings.twilio.auth_token)
            client.calls(self._call_sid).update(status="completed")
            logger.info(f"Hung up call {self._call_sid}")
        except Exception as e:
            logger.error(f"Failed to hang up call {self._call_sid}: {e}")

    def __init__(
        self,
        handler: HandlerType,
        *,
        additional_outputs_handler: Callable | None = None,
        mode: Literal["send-receive", "receive", "send"] = "send-receive",
        modality: Literal["video", "audio", "audio-video"] = "video",
        concurrency_limit: int | None | Literal["default"] = "default",
        time_limit: float | None = None,
        allow_extra_tracks: bool = False,
        rtp_params: dict[str, Any] | None = None,
        rtc_configuration: RTCConfigurationCallable | None = None,
        server_rtc_configuration: dict[str, Any] | None = None,
        track_constraints: dict[str, Any] | None = None,
        additional_inputs: list[Component] | None = None,
        additional_outputs: list[Component] | None = None,
        ui_args: Any | None = None,
        verbose: bool = True,
    ):
        """
        Initialize the VoiceAgentStream instance.

        Args:
            handler: The function to handle incoming stream data and return output data.
            additional_outputs_handler: An optional function to handle updates to additional output components.
            mode: The direction of the stream ('send', 'receive', or 'send-receive').
            modality: The type of media ('video', 'audio', or 'audio-video').
            concurrency_limit: Maximum number of concurrent connections. 'default' maps to 1.
            time_limit: Maximum execution time for the handler function in seconds.
            allow_extra_tracks: If True, allows connections with tracks not matching the modality.
            rtp_params: Optional dictionary of RTP encoding parameters.
            rtc_configuration: Optional Callable or dictionary for RTCPeerConnection configuration (e.g., ICE servers).
                               Required when deploying on Colab or Spaces.
            server_rtc_configuration: Optional dictionary for RTCPeerConnection configuration on the server side.
            track_constraints: Optional dictionary of constraints for media tracks (e.g., resolution, frame rate).
            additional_inputs: Optional list of extra Gradio input components.
            additional_outputs: Optional list of extra Gradio output components. Requires `additional_outputs_handler`.
            ui_args: Optional dictionary to customize the default UI appearance (title, subtitle, icon, etc.).
            verbose: Whether to print verbose logging on startup.
        """
        super().__init__(
            handler=handler,
            additional_outputs_handler=additional_outputs_handler,
            mode=mode,
            modality=modality,
            concurrency_limit=concurrency_limit,
            time_limit=time_limit,
            allow_extra_tracks=allow_extra_tracks,
            rtp_params=rtp_params,
            rtc_configuration=rtc_configuration,
            server_rtc_configuration=server_rtc_configuration,
            track_constraints=track_constraints,
            additional_inputs=additional_inputs,
            additional_outputs=additional_outputs,
            ui_args=ui_args,
            verbose=verbose,
        )
        # Holds references to fire-and-forget tasks so they aren't garbage collected
        # mid-flight (a documented asyncio.create_task gotcha).
        self._background_tasks = set()

    def _spawn_background_task(self, coro) -> None:
        """Run a coroutine fire-and-forget while keeping a strong reference to it."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _set_time_limit_when_live(self, call_sid: str) -> None:
        """Apply Twilio's hard call ``time_limit`` once the call reaches in-progress.

        The incoming-call webhook fires before the call is in-progress, so issuing the
        ``time_limit`` update inline 400s with "Call is not in-progress". Twilio moves the
        call to in-progress within ~1-2s of executing the TwiML, so we retry with a short
        backoff until it accepts the update (or give up after a bounded budget). The blocking
        REST call runs in a thread to keep it off the audio event loop.
        """
        from twilio.rest import Client
        from realtime_phone_agents.config import settings

        client = Client(settings.twilio.account_sid, settings.twilio.auth_token)
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(2.0)
            try:
                await asyncio.to_thread(
                    client.calls(call_sid).update, time_limit=MAX_CALL_DURATION_SECONDS
                )
                logger.info(
                    f"Set call time limit to {MAX_CALL_DURATION_SECONDS}s for {call_sid} "
                    f"(attempt {attempt})"
                )
                return
            except Exception as e:
                if "not in-progress" in str(e).lower() and attempt < max_attempts:
                    logger.debug(
                        f"Call {call_sid} not in-progress yet (attempt {attempt}); retrying"
                    )
                    continue
                logger.warning(
                    f"Failed to set time limit for {call_sid} after {attempt} attempt(s): {e}"
                )
                return

    async def handle_incoming_call(self, request: Request):
        """
        Handle incoming telephone calls (e.g., via Twilio).

        Generates TwiML instructions to connect the incoming call to the
        WebSocket handler (`/telephone/handler`) for audio streaming.

        Args:
            request: The FastAPI Request object for the incoming call webhook.

        Returns:
            An HTMLResponse containing the TwiML instructions as XML.
        """
        from twilio.twiml.voice_response import Connect, VoiceResponse

        from realtime_phone_agents.config import settings

        form = await request.form()
        self._caller_phone = form.get("From")
        self._call_sid = form.get("CallSid")
        logger.info(f"Incoming call from: {self._caller_phone} (SID: {self._call_sid})")

        if self._caller_phone and self._on_caller_phone:
            self._on_caller_phone(self._caller_phone)

        # Enforce a hard Twilio-level call duration cap. The call isn't in-progress yet at
        # webhook time (an inline update 400s "Call is not in-progress"), so apply it from a
        # background task that retries once the call goes live.
        if self._call_sid and settings.twilio.account_sid and settings.twilio.auth_token:
            self._spawn_background_task(self._set_time_limit_when_live(self._call_sid))

        response = VoiceResponse()
        connect = Connect()
        
        # Get hostname from X-Forwarded-Host header (if behind proxy) or fallback to request hostname
        hostname = request.headers.get("x-forwarded-host", request.url.hostname)
        
        path = request.url.path.removesuffix("/telephone/incoming")
        connect.stream(url=f"wss://{hostname}{path}/telephone/handler")
        response.append(connect)
        response.say("The call has been disconnected.")
        return HTMLResponse(content=str(response), media_type="application/xml")
