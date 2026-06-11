import re
import time
from typing import AsyncIterator, List, Optional, Tuple

import numpy as np
from fastrtc import ReplyOnPause, Stream
from fastrtc.reply_on_pause import AlgoOptions
from fastrtc.utils import get_current_context
from realtime_phone_agents.agent.stream import VoiceAgentStream
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from loguru import logger
from opik.integrations.langchain import OpikTracer
from opik import opik_context
import opik

from realtime_phone_agents.agent.tools.property_search import search_property_tool
from realtime_phone_agents.agent.utils import get_tool_call_names, model_has_tool_calls
from realtime_phone_agents.background_effects import get_sound_effect
from realtime_phone_agents.config import settings
from realtime_phone_agents.stt import get_stt_model
from realtime_phone_agents.tts import get_tts_model
from realtime_phone_agents.avatars.registry import get_avatar

AudioChunk = Tuple[int, np.ndarray]  # (sample_rate, samples)

# Surface the groq SDK's retry/backoff. It logs "Retrying request ... in N
# seconds" at INFO on each 429. The app uses loguru, so the stdlib root logger
# has NO handler — setLevel alone gets dropped (stdlib's fallback only emits at
# WARNING). Attach a dedicated stderr handler to the "groq" logger so a
# rate-limit throttle shows up literally instead of only as inflated LLM_HTTP
# wall. propagate=False avoids any double-emit if a root handler appears later.
import logging
import sys

_groq_log = logging.getLogger("groq")
_groq_log.setLevel(logging.INFO)
if not any(getattr(h, "_groq_retry_sink", False) for h in _groq_log.handlers):
    _h = logging.StreamHandler(sys.stderr)
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter("GROQ_SDK %(levelname)s %(name)s: %(message)s"))
    _h._groq_retry_sink = True
    _groq_log.addHandler(_h)
    _groq_log.propagate = False


class _LLMTimingCallback(BaseCallbackHandler):
    """Times the full model HTTP call from start to end.

    on_(chat_model|llm)_start -> on_llm_end brackets the entire langchain_groq
    invocation INCLUDING any internal retry/backoff on 429 rate-limits. Compared
    against GROQ_TIMING (server-side ~0.3s) and ASTREAM_TIMING (invoke->first
    chunk), this splits the silent end-of-call gap:
      - LLM_HTTP wall ≈ ASTREAM gap  -> time is in the Groq HTTP client
        (rate-limit retry/backoff or network), not generation.
      - LLM_HTTP wall ≈ 0.5s but ASTREAM big -> time is OUTSIDE the LLM call
        (Opik callback/decorator or chain overhead).
    """

    def __init__(self) -> None:
        self._starts: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        self._starts[run_id] = time.monotonic()

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        self._starts[run_id] = time.monotonic()

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        t0 = self._starts.pop(run_id, None)
        if t0 is not None:
            logger.info(f"LLM_HTTP wall={time.monotonic() - t0:.3f}s")

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        t0 = self._starts.pop(run_id, None)
        if t0 is not None:
            logger.warning(
                f"LLM_HTTP error wall={time.monotonic() - t0:.3f}s "
                f"{type(error).__name__}: {error}"
            )


@wrap_model_call
async def _trim_history_middleware(request, handler):
    """Trim conversation history to a token budget before each model call.

    Full history stays in the checkpointer (memory is preserved); this only
    caps what each LLM call SEES. Re-sending the whole transcript every turn is
    what drives prompt_tok up (1466->4146 in one call) and pushes us into the
    per-minute rate limit, whose retry/backoff is the silent 10-30s end-of-call
    gap (see LLM_HTTP wall >> GROQ server-side time). System prompt and tool
    schemas are separate ModelRequest fields, so they're untouched here.

    HISTORY_TRIM logs before/after so the effect is visible next to LLM_HTTP.
    """
    budget = settings.history_trim_max_tokens
    if budget <= 0:
        return await handler(request)

    msgs = request.messages
    before_tok = count_tokens_approximately(msgs)
    trimmed = trim_messages(
        msgs,
        max_tokens=budget,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",        # never begin on an orphan AI/Tool message
        end_on=("human", "tool"),  # keep a complete trailing tool sequence
        include_system=False,      # system prompt is a separate request field
        allow_partial=False,
    )
    if len(trimmed) < len(msgs):
        logger.info(
            f"HISTORY_TRIM msgs {len(msgs)}->{len(trimmed)} "
            f"approx_tok {before_tok}->{count_tokens_approximately(trimmed)} "
            f"(budget={budget})"
        )
    return await handler(request.override(messages=trimmed))


class FastRTCAgent:
    """
    Simplified FastRTC agent that encapsulates all dependencies and logic
    for processing audio through speech-to-text, agent reasoning, and text-to-speech.

    This class combines the React agent creation and FastRTC streaming into a single
    cohesive unit, optimized for mobile phone compatibility by avoiding gradio additional_inputs.
    """

    def __init__(
        self,
        tool_use_messages: list[str] | None = None,
        sound_effect_seconds: float = 3.0,
        stt_model=None,
        tts_model=None,
        voice_effect=None,
        thread_id: str = "default",
        fallback_message: str = "I'm sorry, I couldn't find anything useful in the system.",
        avatar: str | None = None,
        tools: List | None = None,
    ):
        """
        Initialize the FastRTC agent with all its dependencies.

        Args:
            tool_use_message: Message to speak when using tools
            sound_effect_seconds: Duration for sound effects when using tools (e.g. keyboard sound)
            stt_model: Speech-to-text model (defaults to get_stt_model())
            tts_model: Text-to-speech model (defaults to get_tts_model())
            voice_effect: Voice effect instance (defaults to get_sound_effect())
            thread_id: Thread ID for agent conversation tracking
            fallback_message: Message to return when no answer is found
            avatar: Avatar for the agent
            tools: List of tools for the agent (defaults to property search tool)
        """
        # Create Opik tracer for LangChain callbacks
        self._opik_tracer = OpikTracer(
            tags=["fastrtc-agent", "realtime-phone"],
            thread_id=thread_id,
        )
        # Times the raw model HTTP call (incl. retries) to isolate the silent gap
        self._llm_timing_cb = _LLMTimingCallback()

        # Dependency injection with sensible defaults
        self._stt_model = stt_model or get_stt_model(settings.stt_model)
        self._tts_model = tts_model or get_tts_model(settings.tts_model)
        self._voice_effect = voice_effect or get_sound_effect()

        self._avatar = get_avatar(avatar or settings.avatar_name)

        # Track tools list for add_tool() support
        self._tools = list(tools) if tools else [search_property_tool]

        # Single checkpointer shared across agent rebuilds to preserve conversation history
        self._checkpointer = InMemorySaver()

        # Create the React agent directly inside the class
        self._react_agent = self._create_react_agent(
            system_prompt=self._avatar.get_system_prompt(),
            tools=self._tools,
        )

        # Configuration - stored as instance variables to avoid gradio additional_inputs
        self._thread_id = thread_id
        self._fallback_message = fallback_message
        self._sound_effect_seconds = sound_effect_seconds

        # Rotating tool use messages so the caller doesn't hear the same phrase every time
        self._tool_use_messages = tool_use_messages or [
            "Let me pull that up for you.",
            "Sure, let me search our listings.",
            "One moment while I look into that.",
            "Let me see what we have.",
            "Good question, let me check on that.",
        ]
        self._tool_use_count = 0
        self._turn_count = 0
        self._max_turns = 20  # ~7 min of conversation, start wrapping up at max_turns - 2

        # Pre-generate greeting audio at startup so callers hear Leo immediately
        self._greeting_audio = self._generate_greeting()

        # Build the FastRTC Stream with the handler
        self._stream = self._build_stream()

    def _generate_greeting(self) -> AudioChunk | None:
        """Pre-generate the avatar's greeting audio at startup for instant playback."""
        self._greeting_text = (
            f"Hey there, this is {self._avatar.name} with Mile High Home Finders. "
            "Thanks for calling! How can I help you today?"
        )
        greeting_text = self._greeting_text
        try:
            logger.info(f"Pre-generating greeting audio: {greeting_text!r}")
            sample_rate, audio = self._tts_model.tts(greeting_text)
            logger.info(
                f"Greeting audio cached: {len(audio)} samples at {sample_rate}Hz"
            )
            return (sample_rate, audio)
        except Exception as e:
            logger.error(f"Failed to pre-generate greeting audio: {e}")
            return None

    def _build_llm(self):
        """Build the conversational LLM for the configured provider.

        Provider switch (settings.llm_provider):
          - "together": Together AI's OpenAI-compatible endpoint via ChatOpenAI.
            Together hosts the SAME model id (openai/gpt-oss-120b). It does NOT
            support Groq's `reasoning_format` param — but it doesn't need it:
            Together returns the gpt-oss reasoning in a SEPARATE `reasoning` field,
            so `content` (read by _extract_final_text) is already the clean final
            answer and the "Leo speaks his thoughts" leak does not recur. Moving off
            Groq's throttled free tier is also the fix/A-B test for the silent
            end-of-call latency gaps (suspected 429 retry-backoff).
          - "groq": original ChatGroq path, kept as a fallback.

        Shared: temperature/frequency_penalty/max_retries match the prior tuning.
        max_tokens is a runaway backstop only (set high enough to never truncate a
        real answer); bumped to 1024 on Together since reasoning-token accounting
        against the cap differs from Groq and we don't want a mid-call truncation.
        """
        if settings.llm_provider == "groq":
            # request_timeout bounds each attempt so backoff can't stall the call
            # indefinitely (was None = unbounded). max_retries kept at 2; the groq
            # SDK logs each 429 retry (we raise its logger to INFO at import time),
            # so a throttle event now shows up literally instead of as silent wall.
            return ChatGroq(
                model=settings.groq.model,
                api_key=settings.groq.api_key,
                reasoning_format="hidden",
                temperature=0.5,
                max_tokens=512,
                max_retries=2,
                request_timeout=30.0,
                model_kwargs={"frequency_penalty": 0.6},
            )

        # Lazy import: langchain_openai is only needed for the Together path and is
        # not in the built image (pip-installed into the container ad hoc on Jun 10,
        # lost on recreate). Importing at module top crashed startup even on the
        # default Groq path. Keep it scoped to the branch that actually uses it.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.together_llm_model,
            api_key=settings.together.api_key,
            base_url=settings.together.api_url,
            temperature=0.5,
            max_tokens=1024,
            frequency_penalty=0.6,
            max_retries=2,
        )

    def _create_react_agent(
        self,
        system_prompt: str | None = None,
        tools: List | None = None,
    ):
        """
        Create and return a LangChain agent with Groq + InMemorySaver + tools.

        Args:
            system_prompt: Custom system prompt (defaults to DEFAULT_SYSTEM_PROMPT)
            tools: List of tools (defaults to [search_property_mock_tool])

        Returns:
            Configured LangChain agent
        """
        llm = self._build_llm()

        tools = tools or [search_property_tool]

        agent = create_agent(
            llm,
            checkpointer=self._checkpointer,
            system_prompt=system_prompt,
            tools=tools,
            middleware=[_trim_history_middleware],
        )
        return agent

    def _build_stream(self) -> Stream:
        """
        Build and configure the FastRTC Stream with the agent handler.
        Uses instance variables instead of gradio additional_inputs for mobile compatibility.

        Returns:
            Configured Stream instance
        """
        greeting_audio = self._greeting_audio

        async def handler_wrapper(audio: AudioChunk) -> AsyncIterator[AudioChunk]:
            """Handler that uses instance variables directly."""
            async for chunk in self._process_audio(audio):
                yield chunk

        async def greeting_startup():
            """Yield cached greeting audio on WebSocket connect."""
            if greeting_audio is not None:
                logger.info("⏱️ greeting_startup entered, emitting cached greeting audio now")
                yield greeting_audio
                logger.info("⏱️ greeting audio emitted to caller")

        startup_fn = greeting_startup if greeting_audio is not None else None

        return VoiceAgentStream(
            handler=ReplyOnPause(
                handler_wrapper,
                startup_fn=startup_fn,
                algo_options=AlgoOptions(
                    audio_chunk_duration=0.6,
                    started_talking_threshold=0.2,
                    speech_threshold=0.3,
                ),
            ),
            modality="audio",
            mode="send-receive",
        )

    @opik.track(name="generate-avatar-response", capture_input=False, capture_output=False)
    async def _process_audio(
        self,
        audio: AudioChunk,
    ) -> AsyncIterator[AudioChunk]:
        """
        Process audio input through the complete pipeline:
        STT -> Agent Reasoning -> TTS with effects.
        Uses instance variables for configuration (tool_use_message, sound_effect_seconds).

        Args:
            audio: Input audio chunk (sample_rate, samples)

        Yields:
            Audio chunks to be played back to the user
        """

        # Use FastRTC's per-connection context as thread ID so each call is isolated
        try:
            context = get_current_context()
            if context.webrtc_id != self._thread_id:
                self._thread_id = context.webrtc_id
                self._opik_tracer = OpikTracer(
                    tags=["fastrtc-agent", "realtime-phone"],
                    thread_id=self._thread_id,
                )
                self._tool_use_count = 0
                self._turn_count = 0
                self._greeting_seeded = False
                logger.info(f"New call session: {self._thread_id}")
        except Exception:
            pass  # Fall back to existing thread_id if context unavailable

        # Step 1: Transcribe audio to text
        transcription = await self._transcribe(audio)
        logger.info(f"Transcription: {transcription}")

        # Track turns and enforce call limits
        self._turn_count += 1
        logger.info(f"Turn {self._turn_count}/{self._max_turns}")

        if self._turn_count > self._max_turns:
            # Hard cutoff — politely end the call, then hang up
            goodbye = (
                "I've really enjoyed chatting with you, but I need to wrap up. "
                "Feel free to call back anytime. Have a great day!"
            )
            async for audio_chunk in self._synthesize_speech(goodbye):
                yield audio_chunk
            # Actually disconnect the call via Twilio
            if isinstance(self._stream, VoiceAgentStream):
                self._stream.hang_up()
            return

        # Inject wrap-up nudge when approaching the limit
        if self._turn_count == self._max_turns - 2:
            transcription += (
                "\n\n[SYSTEM: This call is approaching the time limit. "
                "Start wrapping up naturally — summarize any next steps, "
                "offer to send details via SMS if a showing was scheduled, "
                "and say goodbye warmly.]"
            )

        # Step 2: Process with agent and stream responses
        async for audio_chunk in self._process_with_agent(transcription):
            if audio_chunk is not None:
                yield audio_chunk

        # Step 3: Speak final answer
        final_response = await self._get_final_response()
        logger.info(f"Final response: {final_response}")

        if final_response:
            async for audio_chunk in self._synthesize_speech(final_response):
                yield audio_chunk

    @opik.track(name="stt-transcription", capture_input=False, capture_output=True)
    async def _transcribe(self, audio: AudioChunk) -> str:
        """
        Transcribe audio to text using STT model.

        Args:
            audio: Audio chunk to transcribe

        Returns:
            Transcribed text
        """
        return self._stt_model.stt(audio)

    @opik.track(name="generate-agent-response")
    async def _process_with_agent(
        self,
        transcription: str,
    ) -> AsyncIterator[Optional[AudioChunk]]:
        """
        Process transcription through the agent and handle tool calls.
        Uses instance variables for tool_use_message and sound_effect_seconds.

        Args:
            transcription: User's transcribed message

        Yields:
            Audio chunks for tool use messages and effects
        """
        final_text: str | None = None
        spoke_tool_message = False

        # On the first turn, prepend the greeting as an AI message so the LLM
        # knows it already introduced itself. This avoids ainvoke (which runs
        # the full graph and can hallucinate tool calls) and aupdate_state
        # (which can corrupt graph routing state).
        messages = []
        if not getattr(self, "_greeting_seeded", True) and self._greeting_text:
            messages.append({"role": "assistant", "content": self._greeting_text})
            self._greeting_seeded = True
            logger.info("Prepended greeting to first turn messages")
        messages.append({"role": "user", "content": transcription})

        # Stream LangChain agent updates with Opik tracing.
        # ASTREAM_TIMING: measure wall-clock from invoking the agent to the first
        # streamed chunk. Compared against GROQ_TIMING (server-side ~0.4s), a large
        # delta here proves the gap is event-loop starvation, not Groq generation.
        _astream_start = time.monotonic()
        _first_chunk_logged = False
        _tool_round = 0
        async for chunk in self._react_agent.astream(
            {"messages": messages},
            {
                "configurable": {"thread_id": self._thread_id},
                "callbacks": [self._opik_tracer, self._llm_timing_cb]
            },
            stream_mode="updates",
        ):
            if not _first_chunk_logged:
                _first_chunk_logged = True
                logger.info(
                    f"ASTREAM_TIMING first_chunk={time.monotonic() - _astream_start:.3f}s"
                )
            for step, data in chunk.items():
                # Log Groq server-side timing for every LLM call (a tool turn
                # makes two, so this fires per-call, not per-turn).
                if step == "model":
                    self._log_groq_timing(data)

                # Handle tool calls — only speak on the first tool call in
                # this turn so the caller doesn't hear repeated messages
                # when the LLM chains multiple searches.
                if step == "model" and model_has_tool_calls(data):
                    tool_names = get_tool_call_names(data)
                    is_sms = any(n == "send_sms" for n in tool_names)

                    # TOOL_LOOP: count tool-call rounds within a single turn.
                    # >1 means the agent re-queried (the multi-search loop that
                    # stacked ~25s searches into a minute of dead air).
                    _tool_round += 1
                    logger.info(f"TOOL_LOOP round={_tool_round} tools={tool_names}")

                    if not spoke_tool_message:
                        spoke_tool_message = True
                        # If the LLM included text alongside the tool call,
                        # speak that instead of a canned message so the
                        # conversation flows naturally before the search.
                        model_text = self._extract_final_text(data)
                        if model_text and model_text.strip():
                            message = self._clip_at_first_question(model_text.strip())
                        elif is_sms:
                            message = "Sending that over to you now."
                        else:
                            message = self._tool_use_messages[self._tool_use_count % len(self._tool_use_messages)]
                        self._tool_use_count += 1
                        async for audio_chunk in self._synthesize_speech(message):
                            yield audio_chunk

                        # Play sound effect (skip for SMS — it's near-instant)
                        if self._sound_effect_seconds > 0 and not is_sms:
                            async for effect_chunk in self._play_sound_effect():
                                yield effect_chunk

                # Capture final text only from model steps without tool calls.
                # Tool-call steps may have preamble text (e.g. "let me check")
                # that we already spoke above — don't repeat it as the final answer.
                if step == "model" and not model_has_tool_calls(data):
                    final_text = self._extract_final_text(data)

        # Store final text for later retrieval
        self._last_final_text = final_text

        if final_text:
            opik_context.update_current_trace(
                thread_id=self._thread_id,
                input={"transcription": transcription},
                output={"final_text": final_text},
            )

    def _extract_final_text(self, model_step_data) -> Optional[str]:
        """
        Extract the final text response from model step data.

        Args:
            model_step_data: Data from the model step

        Returns:
            Extracted text or None
        """
        msgs = model_step_data.get("messages", [])
        if isinstance(msgs, list) and len(msgs) > 0:
            return getattr(msgs[0], "content", None)
        return None

    @staticmethod
    def _log_groq_timing(model_step_data) -> None:
        """Log Groq's per-call server-side timing to split the LLM latency spike.

        Jun 9: the 20-28s ChatGroq spikes (worst on the LAST turns of long calls)
        could be Groq-side queueing (rate limit), prompt processing (context growth),
        or real generation. Groq returns all three in response_metadata.token_usage
        (queue_time / prompt_time / completion_time), plus reasoning_tokens. Logging
        them per call lets the data pick the lever instead of guessing.
        """
        msgs = model_step_data.get("messages", [])
        if not (isinstance(msgs, list) and msgs):
            return
        meta = getattr(msgs[0], "response_metadata", None) or {}
        tu = meta.get("token_usage") or {}
        if not tu:
            logger.info("GROQ_TIMING: no token_usage in response_metadata "
                        f"(keys={list(meta.keys())})")
            return
        details = tu.get("completion_tokens_details") or {}
        logger.info(
            "GROQ_TIMING "
            f"queue={tu.get('queue_time', 0):.3f}s "
            f"prompt={tu.get('prompt_time', 0):.3f}s "
            f"completion={tu.get('completion_time', 0):.3f}s "
            f"total={tu.get('total_time', 0):.3f}s | "
            f"prompt_tok={tu.get('prompt_tokens', 0)} "
            f"completion_tok={tu.get('completion_tokens', 0)} "
            f"reasoning_tok={details.get('reasoning_tokens', 0)}"
        )

    async def _get_final_response(self) -> str:
        """
        Get the final response text to speak to the user.

        Returns:
            Final response text
        """
        final = getattr(self, "_last_final_text", None) or self._fallback_message
        return self._clip_at_first_question(final)

    @staticmethod
    def _clip_at_first_question(text: str) -> str:
        """Enforce one-question-at-a-time by truncating at the first question mark.

        Even with reasoning_format="hidden" (which fixed the analysis-channel
        leak), gpt-oss still degenerates on scheduling/confirmation turns by
        role-playing both sides of several future turns in one reply (e.g.
        "Which day works?Perfect, let's lock it in.Shall I text you?..."). That
        collapses the turn-by-turn exchange the scheduling flow needs and,
        critically, makes Leo narrate sending the SMS without ever reaching the
        real send_sms tool turn.

        Cutting at the first "?" keeps the lead-in plus the single question and
        drops the invented continuation. Property readouts are unaffected —
        their only question sits at the end ("...Want more details or a
        showing?"). Replies with no question (confirmations, "the text is on its
        way") are returned unchanged.
        """
        if not text:
            return text
        idx = text.find("?")
        if idx == -1:
            return text
        return text[: idx + 1].strip()

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for incremental TTS.

        Splits on sentence-ending punctuation (.!?) followed by a space, plus a
        run-on guard: ? or ! immediately followed by a capital letter (no space).
        Traces showed the model joining restatements as "...works for you?Sounds
        good..." which dodged the space-based split and played as one long breath.
        We deliberately do NOT split a bare "." without a space, to protect
        "p.m.", decimals, and abbreviations.
        """
        parts = re.split(r'(?<=[.!?])\s+|(?<=[?!])(?=[A-Z])', text.strip())
        return [p.strip() for p in parts if p.strip()]

    @opik.track(name="tts-generation", capture_input=True, capture_output=False)
    async def _synthesize_speech(self, text: str) -> AsyncIterator[AudioChunk]:
        """
        Convert text to speech audio chunks.

        Splits text into sentences and synthesizes each one separately so the
        caller hears the first sentence while later ones are still generating.

        Args:
            text: Text to synthesize

        Yields:
            Audio chunks
        """
        sentences = self._split_sentences(text)
        for sentence in sentences:
            async for audio_chunk in self._tts_model.stream_tts(sentence):
                yield audio_chunk

    @opik.track(name="play-sound-effect", capture_input=False, capture_output=False)
    async def _play_sound_effect(self) -> AsyncIterator[AudioChunk]:
        """
        Play the configured sound effect.

        Yields:
            Audio chunks for the sound effect
        """
        async for effect_chunk in self._voice_effect.stream():
            yield effect_chunk

    @property
    def stream(self) -> Stream:
        """
        Expose the FastRTC Stream instance.

        Returns:
            The configured Stream instance
        """
        return self._stream

    @property
    def stt_model(self):
        """Get the speech-to-text model."""
        return self._stt_model

    @property
    def tts_model(self):
        """Get the text-to-speech model."""
        return self._tts_model

    @property
    def react_agent(self):
        """Get the React agent."""
        return self._react_agent

    @property
    def voice_effect(self):
        """Get the voice effect."""
        return self._voice_effect

    @property
    def opik_tracer(self):
        """Get the Opik tracer."""
        return self._opik_tracer

    def set_thread_id(self, thread_id: str) -> None:
        """
        Update the thread ID for conversation tracking.

        Args:
            thread_id: New thread ID
        """
        self._thread_id = thread_id

    def add_tool(self, tool) -> None:
        """Add a tool to the agent and rebuild the React agent with the updated tool list.

        This is useful for tools that need a reference to the stream instance
        (e.g. SMS tool), which isn't available until after __init__ completes.

        Args:
            tool: A LangChain tool to add
        """
        self._tools.append(tool)
        self._react_agent = self._create_react_agent(
            system_prompt=self._avatar.get_system_prompt(),
            tools=self._tools,
        )

    def set_caller_phone(self, phone: str) -> None:
        """Rebuild the react agent with the caller's phone number injected into the system prompt.

        Called when a new inbound call arrives so the LLM knows the caller's actual number.
        """
        logger.info(f"⏱️ set_caller_phone START - rebuilding agent with caller phone: {phone}")
        _t0 = time.monotonic()
        self._react_agent = self._create_react_agent(
            system_prompt=self._avatar.get_system_prompt(caller_phone=phone),
            tools=self._tools,
        )
        logger.info(f"⏱️ set_caller_phone DONE - rebuild took {time.monotonic() - _t0:.2f}s")

    def set_fallback_message(self, message: str) -> None:
        """
        Update the fallback message.

        Args:
            message: New fallback message
        """
        self._fallback_message = message

    def set_sound_effect_seconds(self, seconds: float) -> None:
        """
        Update the sound effect duration.

        Args:
            seconds: New sound effect duration
        """
        self._sound_effect_seconds = seconds
