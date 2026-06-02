# Leo on OpenAI Realtime API - Implementation Spec

> **STATUS: SHELVED (2026-03-27)** - ~$2.50/call on gpt-realtime-mini is too expensive
> for a portfolio demo you want to call freely. Revisit when pricing drops.
> The modular stack (Groq + Cartesia + FastRTC) runs at ~$0.05/call.

## Overview

Build a second version of Leo using OpenAI's Realtime API, running alongside the
existing modular stack (Groq STT + Groq LLM + Cartesia TTS + FastRTC). The goal is
a working comparison that demonstrates both approaches and deepens understanding of
managed vs. component-level voice AI architectures.

The existing codebase stays untouched. This version lives in its own module and shares
only what makes sense (avatar prompts, property search, config patterns).

---

## What Changes vs. Current Stack

| Layer | Current (modular) | OpenAI Realtime |
|-------|-------------------|-----------------|
| STT | Groq Whisper (separate hop) | Native - model processes audio tokens directly |
| LLM | Groq (llama/mixtral) | GPT Realtime (gpt-realtime or gpt-realtime-mini) |
| TTS | Cartesia Sonic 3 via Together AI | Native - model generates audio tokens directly |
| Transport | FastRTC WebRTC + Twilio WebSocket bridge | WebSocket to OpenAI + Twilio WebSocket bridge |
| VAD/Barge-in | FastRTC AlgoOptions (pause detection only) | Semantic VAD with native interruption handling |
| Tool calling | LangChain React agent + astream | OpenAI function calling via Realtime events |
| Conversation state | LangGraph InMemorySaver checkpointer | OpenAI session state (server-managed) |
| Sound effects | Keyboard typing audio during tool use | Not needed - latency is low enough to skip |
| Greeting | Pre-generated TTS audio cached at startup | First server event can include greeting text |

**What stays the same:**
- Avatar/persona (Leo's system prompt from `avatars/base.py`)
- Property search tool (Superlinked + Qdrant)
- SMS tool (Twilio)
- Twilio inbound/outbound telephony
- Opik observability (trace events manually)
- FastAPI for webhooks and health
- Docker + Qdrant infrastructure

---

## Architecture

```
Incoming Twilio Call
    |
/voice/telephone/incoming (TwiML webhook - reuse existing)
    |-- Extract caller phone + call SID
    |-- Set call time limit
    |-- Connect to /voice/realtime/handler (new WebSocket endpoint)

Twilio WebSocket <---> RealtimeAgent <---> OpenAI Realtime API (WebSocket)
    |                       |
    |                       |-- Receives audio from Twilio, forwards to OpenAI
    |                       |-- Receives audio from OpenAI, forwards to Twilio
    |                       |-- Handles function calls locally (property search, SMS)
    |                       |-- Manages session config (voice, instructions, tools)
    |
    |-- Twilio sends mulaw/8kHz audio chunks
    |-- Twilio expects mulaw/8kHz audio chunks back
```

### Connection Flow

1. Twilio inbound call hits existing webhook
2. TwiML connects caller to a new WebSocket endpoint (`/voice/realtime/handler`)
3. Server opens a WebSocket to OpenAI Realtime API (`wss://api.openai.com/v1/realtime`)
4. Server bridges audio between Twilio and OpenAI:
   - Twilio audio (mulaw 8kHz base64) -> decode -> re-encode if needed -> forward to OpenAI
   - OpenAI audio (PCM16 24kHz base64) -> decode -> resample to 8kHz mulaw -> forward to Twilio
5. Server intercepts function call events from OpenAI, executes tools locally, returns results
6. On hangup, server closes both WebSocket connections

### Audio Format Bridge

This is the trickiest part. Twilio sends/receives mulaw 8kHz. OpenAI Realtime supports
PCM16 and mulaw at 24kHz (default) or 8kHz.

**Preferred approach:** Configure OpenAI session with `input_audio_format: "g711_ulaw"` and
`output_audio_format: "g711_ulaw"` at 8kHz if supported. This eliminates resampling entirely.

**Fallback:** If OpenAI requires 24kHz, resample in the bridge layer:
- Twilio -> upsample 8kHz to 24kHz -> OpenAI
- OpenAI -> downsample 24kHz to 8kHz -> Twilio

Use `audioop` (stdlib) or numpy for the conversion. Keep it simple - no heavy DSP.

---

## New Files to Create

```
src/realtime_phone_agents/
    agent/
        realtime_agent.py       # RealtimeAgent class (replaces FastRTCAgent for this mode)
        realtime_stream.py      # WebSocket bridge: Twilio <-> OpenAI Realtime
    api/
        routes/
            voice_realtime.py   # New route: /voice/realtime/* endpoints
```

### `realtime_agent.py` - Core Agent

Responsibilities:
- Open and manage WebSocket connection to OpenAI Realtime API
- Configure session (voice, instructions, tools, VAD settings)
- Handle the event loop: receive events, dispatch handlers
- Execute function calls (property search, SMS) and return results
- Track turn count and enforce call limits
- Inject caller phone into instructions on new call

Key events to handle:

| Event | Action |
|-------|--------|
| `session.created` | Send `session.update` with config |
| `input_audio_buffer.speech_started` | Log, optionally track for analytics |
| `input_audio_buffer.speech_stopped` | Log turn boundary |
| `response.audio.delta` | Forward decoded audio to Twilio |
| `response.audio_transcript.delta` | Log for observability |
| `response.output_item.done` | Check for function calls |
| `response.done` | Log completion, update turn count |
| `error` | Log and handle gracefully |

```python
class RealtimeAgent:
    """OpenAI Realtime API agent - manages session and event handling."""

    def __init__(
        self,
        avatar: str | None = None,
        tools: list | None = None,
        max_turns: int = 20,
    ):
        self._avatar = get_avatar(avatar or settings.avatar_name)
        self._tools = tools or [search_property_tool]
        self._max_turns = max_turns
        self._turn_count = 0
        self._caller_phone: str | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def connect(self) -> None:
        """Open WebSocket to OpenAI Realtime API and configure session."""

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Forward audio chunk from Twilio to OpenAI."""

    async def receive_events(self) -> AsyncIterator[dict]:
        """Yield parsed events from OpenAI WebSocket."""

    async def handle_function_call(self, call_id: str, name: str, args: dict) -> str:
        """Execute a function call and return the result."""

    async def close(self) -> None:
        """Clean up WebSocket connection."""

    def get_session_config(self) -> dict:
        """Build the session.update payload."""
```

### `realtime_stream.py` - Twilio/OpenAI Bridge

Responsibilities:
- Accept Twilio WebSocket connection
- Open OpenAI Realtime WebSocket
- Run two concurrent tasks:
  1. Twilio -> OpenAI: receive Twilio audio, forward as `input_audio_buffer.append`
  2. OpenAI -> Twilio: receive events, forward audio back, handle function calls
- Handle disconnection from either side
- Audio format conversion if needed

```python
class RealtimeStream:
    """Bridges Twilio WebSocket audio with OpenAI Realtime API."""

    def __init__(self, agent: RealtimeAgent):
        self._agent = agent

    async def handle_twilio_ws(self, websocket: WebSocket) -> None:
        """Main handler for Twilio WebSocket connection."""

    async def _twilio_to_openai(self, twilio_ws, openai_ws) -> None:
        """Forward Twilio audio to OpenAI."""

    async def _openai_to_twilio(self, openai_ws, twilio_ws) -> None:
        """Forward OpenAI audio/events back to Twilio."""

    def hang_up(self) -> None:
        """Terminate the active call via Twilio REST API."""
```

### `voice_realtime.py` - API Routes

```python
router = APIRouter(prefix="/voice/realtime", tags=["voice-realtime"])

@router.post("/incoming")
async def incoming_call(request: Request):
    """Twilio webhook - returns TwiML pointing to /voice/realtime/handler."""

@router.websocket("/handler")
async def websocket_handler(websocket: WebSocket):
    """WebSocket endpoint - bridges Twilio and OpenAI Realtime."""
```

---

## Session Configuration

Sent as `session.update` after `session.created`:

```json
{
    "type": "session.update",
    "session": {
        "instructions": "<Leo system prompt with caller_phone injected>",
        "voice": "ash",
        "input_audio_format": "g711_ulaw",
        "output_audio_format": "g711_ulaw",
        "input_audio_transcription": {
            "model": "gpt-realtime-mini"
        },
        "turn_detection": {
            "type": "semantic_vad",
            "eagerness": "medium",
            "interrupt_response": true,
            "create_response": true
        },
        "tools": [
            {
                "type": "function",
                "name": "search_property",
                "description": "Search Denver real estate listings by natural language query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query for properties"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return",
                            "default": 1
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "type": "function",
                "name": "send_sms",
                "description": "Send an SMS with property/showing details to the caller.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone_number": {
                            "type": "string",
                            "description": "Recipient phone number in E.164 format"
                        },
                        "message": {
                            "type": "string",
                            "description": "SMS message body with property details"
                        }
                    },
                    "required": ["phone_number", "message"]
                }
            }
        ]
    }
}
```

### Voice Selection

OpenAI offers: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar.
Recommendation: **ash** or **echo** for a natural male voice that fits Leo's personality.
Test a few and pick based on energy match. `marin` and `cedar` are flagged as highest quality.

### VAD Tuning

Start with `"eagerness": "medium"` and adjust based on testing:
- If Leo talks over callers: reduce eagerness or set to `"low"`
- If Leo waits too long to respond: increase to `"high"`
- Semantic VAD should handle most cases better than FastRTC's threshold-based approach

---

## Function Call Flow

When the model decides to call a tool:

1. OpenAI sends `response.output_item.done` with `type: "function_call"`
2. Server extracts `call_id`, `name`, and `arguments`
3. Server executes the function locally:
   - `search_property` -> call existing `PropertySearchService.search_properties()`
   - `send_sms` -> call existing Twilio SMS logic from `agent/tools/sms.py`
4. Server sends `conversation.item.create` with the function output
5. Server sends `response.create` to trigger the model to continue

```python
# Pseudocode for function call handling
async def handle_function_call(self, call_id, name, arguments):
    if name == "search_property":
        results = await property_service.search_properties(
            query=arguments["query"],
            limit=arguments.get("limit", 1)
        )
        output = json.dumps(results)
    elif name == "send_sms":
        result = send_sms_via_twilio(
            to=arguments["phone_number"],
            body=arguments["message"]
        )
        output = json.dumps({"status": "sent", "sid": result.sid})

    # Send result back to OpenAI
    await self._ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output
        }
    }))
    await self._ws.send(json.dumps({"type": "response.create"}))
```

---

## Call Limit Enforcement

Same logic as current stack, adapted to event-driven model:

- Track turns by counting `response.done` events
- At `max_turns - 2`: inject a system message via `conversation.item.create` nudging Leo to wrap up
- At `max_turns`: send a final goodbye message, then close both WebSockets and call `hang_up()`
- Twilio-level time limit still applies as a hard backstop

---

## Config Changes

Add to `config.py`:

```python
class OpenAIRealtimeSettings(BaseModel):
    api_key: str = ""           # OPENAI_REALTIME__API_KEY
    model: str = "gpt-realtime-mini"  # Start with mini for cost
    voice: str = "ash"
    eagerness: str = "medium"   # VAD eagerness: low, medium, high

class Settings(BaseSettings):
    # ... existing fields ...
    openai_realtime: OpenAIRealtimeSettings = OpenAIRealtimeSettings()
    agent_mode: str = "fastrtc"  # "fastrtc" or "realtime" - controls which agent starts
```

The `agent_mode` setting controls which agent the API boots. Both share the same
Twilio number and webhooks - just different WebSocket handlers.

---

## Observability

No LangChain in this path, so Opik integration changes:

- Use `opik.track` decorators on key methods (connect, handle_function_call, etc.)
- Log all OpenAI events with timestamps for latency analysis
- Capture input transcriptions (`response.audio_transcript.delta`) and output text
- Tag traces with `["realtime-agent", "openai-realtime"]` to distinguish from modular stack

Key metrics to compare against the modular stack:
- **Time to first audio byte** (user stops talking -> first audio chunk back)
- **End-to-end turn latency** (user stops -> full response complete)
- **Interruption success rate** (does barge-in actually work well?)
- **Tool call round-trip time** (function call event -> response resumes)

---

## Dependencies to Add

```toml
# pyproject.toml additions
websockets = ">=14.0"   # OpenAI Realtime WebSocket client
# openai SDK not strictly needed - we're using raw WebSocket events
# But could use it for ephemeral token generation if we add browser support later
```

No new heavy dependencies. The OpenAI Realtime API is just WebSocket + JSON events.

---

## Greeting Strategy

Current stack pre-generates greeting audio at startup. With Realtime API:

**Option A - Let the model greet naturally:**
After session config, send `response.create` with no user input. The model will
speak based on the system prompt. Simplest approach but adds ~1s latency on connect.

**Option B - Inject greeting as first conversation item:**
```json
{
    "type": "conversation.item.create",
    "item": {
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "input_audio",
                "audio": "<base64 pre-generated greeting>"
            }
        ]
    }
}
```
This puts the greeting in conversation history without the model generating it.
Then the first real response knows Leo already introduced himself.

**Recommendation:** Start with Option A. It's simpler and the Realtime API is fast
enough that the greeting latency should be acceptable. If it's too slow, switch to B.

---

## Cost Estimate

Using `gpt-realtime-mini` (recommended starting point):

| Component | Cost per 1M tokens |
|-----------|-------------------|
| Audio input | $10.00 |
| Audio output | $20.00 |
| Text input (instructions) | $0.60 |
| Cached text input | $0.06 |

Rough per-call estimate (5 min call, ~20 turns):
- Audio input: ~50K tokens -> ~$0.50
- Audio output: ~100K tokens -> ~$2.00
- Text (instructions + tool results): ~10K tokens -> ~$0.01
- **Total: ~$2.50 per 5-min call**

Compare to current stack:
- Groq STT: free tier
- Groq LLM: free tier
- Together AI TTS: ~$0.01-0.05 per call
- **Total: ~$0.05 per call**

The Realtime API is significantly more expensive. Fine for a portfolio demo, not for
high-volume production without budget planning. Start with mini model and set a
spending cap in the OpenAI dashboard.

---

## Implementation Order

1. **Config + dependencies** - Add OpenAI Realtime settings, install websockets
2. **RealtimeAgent** - Session management, event loop, function call handling
3. **RealtimeStream** - Twilio/OpenAI audio bridge with format conversion
4. **API routes** - New WebSocket endpoint, TwiML for realtime path
5. **Wire up in main.py** - Mount new routes, respect `agent_mode` setting
6. **Test with Twilio** - Inbound call end-to-end
7. **Observability** - Opik tracing, latency metrics
8. **Tune** - VAD eagerness, voice selection, greeting strategy

---

## What This Proves in Your Portfolio

- You can evaluate build-vs-buy tradeoffs in voice AI (not just pick one)
- You understand both component-level architecture AND managed API integration
- You can articulate latency, cost, and control tradeoffs with real data
- The modular stack shows depth; the Realtime version shows pragmatism
- Side-by-side comparison is a compelling demo and interview talking point

---

## Open Questions

- [ ] Does OpenAI Realtime support mulaw 8kHz natively, or do we need to resample?
- [ ] Which voice best matches Leo's personality? Need to test ash, echo, marin, cedar
- [ ] Is semantic VAD aggressive enough for natural conversation, or will we need manual mode?
- [ ] Can we share the same Twilio phone number with a routing toggle, or do we need a second number?
- [ ] What's the actual token usage per call? The estimate above is rough - need real data
