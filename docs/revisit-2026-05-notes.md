# Leo Revisit - Session Notes & Plan (2026-05-29)

> Picking the project back up after ~2 months away (last tested March 21). This doc
> captures the full baseline verification, the live-call trace diagnosis, the operational
> gotchas, and the plan ahead. Strategic context: this is FDE/Cortex-fluency practice -
> trace-driven latency + prompt/context debugging on a live agent (see `ryan-hq/ideal-role.md`).

---

## ▶ START HERE NEXT SESSION (last worked: Jun 1 2026)

**Done & shipped (on `main`, pushed to `backup`):**
- ✅ Repetition bug fixed + validated in-call (commit `b94dbcf`). See Jun 1 update below.

**Open, in priority order:**
1. **Call-answer latency (18-35s) - biggest user-facing issue.** Diagnosed to the
   FastRTC→Twilio transport (our greeting emits in ~0.1s; the dead air is downstream,
   startup-only). NOT fixable in Leo's code. **Next step:** pick one - (A) instrument
   fastrtc `_emit_loop` (queue depth + per-chunk send timestamps), or (B) browser A/B
   via FastRTC's WebRTC test UI (bypasses Twilio + ngrok) to convict/exonerate ngrok.
   Full context: Jun 1 update below + memory `leo-answer-latency-in-transport`.
2. **Per-turn latency:** TTS variance (1.7-12s) is the main per-turn dead air; property
   search regressed to 5-14s (was ~4s); occasional ~6.8s LLM spike (try `reasoning_effort="low"`).

**To get running again:** `DOCKER_API_VERSION=1.41 docker compose up -d` (NOT `--build`,
it hangs - existing image is fine), wait for `/health`, then POST `/superlinked/ingest`.
Trace tool: `/tmp/analyze_calls.py` (copy into container, run with `/app/.venv/bin/python`).

---

## TL;DR

- **Baseline is healthy.** Full pipeline verified end to end, including a real phone call
  with two SMS-confirmed showings booked in one call.
- **Two real issues found via Opik traces**, both with clean root causes and cheap fixes:
  1. End-of-call **repetition/run-on** = uncapped LLM generation (`ChatGroq` has no `max_tokens`).
  2. **3-4s dead air** before each response = **STT is ~2s every turn** (biggest fixed cost).
- **Plan:** cap the LLM + swap STT to a faster model, re-test the same call, compare traces.
  Open tradeoffs to decide first (see Plan section).

---

## UPDATE - Jun 1 2026 session

Made 8 more test calls and reviewed logs + Opik traces before changing anything. Outcomes:

### Corrections to the notes above
- **Runtime model is `openai/gpt-oss-120b`** (set in `.env` via `GROQ__MODEL`), not `20b`.
  `config.py`'s default is still `20b` but `.env` overrides it.
- **gpt-oss is a reasoning model** - reasoning tokens count toward `max_tokens`. So the
  original "set `max_tokens=150`" idea was a latent bug: it would truncate real answers
  (a full property readout ~130 tokens + reasoning ~150 > 150). max_tokens is a weak
  anti-ramble lever anyway - the bad outputs (~100 tokens) aren't much longer than good ones.

### Repetition - CONFIRMED persistent, then FIXED
Appeared in at least 3 of 5 conversational calls, always on **scheduling/confirmation turns**.
Two flavors, same root cause (uncapped generation): literal restatements, and the worse
"role-play" mode where the model writes BOTH sides of several future turns in one reply
("...works for you?Sounds good. Any must-have features?..."). The `?Word` joins (no space)
dodge the splitter and play as one 10-12s breath = repetition and worst-latency are one bug.

**Fix applied** (`fastrtc_agent.py`):
- LLM: `frequency_penalty=0.6` (primary lever, via `model_kwargs`), `temperature=0.5`,
  `max_tokens=512` (runaway backstop only - high so it never truncates answer+reasoning).
- `_split_sentences`: now also splits `?`/`!` immediately followed by a capital, so a run-on
  can't play as one breath. Deliberately does NOT split a bare `.` (protects "p.m."/decimals).
- Verified Groq accepts `frequency_penalty` on this model via a live invoke. **Still needs a
  before/after call batch to confirm in-call** (use `/tmp/analyze_calls.py`, trace-only REP/SLOW).

### Per-turn latency - re-measured
STT improved to ~0.5-1.7s (no longer the bottleneck). LLM usually <2s, occasional ~6.8s spike
(`reasoning_effort="low"` is a candidate lever). TTS highly variable 1.7-12s (main per-turn
dead air now). **Property search REGRESSED to 5-14s** (was ~4s) - worth a look, but it's behind
the "let me check" preamble so not pure dead air.

### NEW headline issue - call-ANSWER latency (18-35s) is in the transport, not Leo
Caller hears Twilio's "one moment please" at ~2s, then 18-35s of dead air before Leo's greeting.
Instrumented the connect path (⏱️ logs): agent rebuild = 0.04-0.09s; greeting is **emitted
~0.1s after the WebSocket opens**, every call (launched on Twilio's `start` event with
`stream_id` set, so not dropped). So **all the dead air is downstream of our `yield`**, in the
FastRTC→ngrok→Twilio path, and it's **startup-only** (conversations flow fine after). FastRTC's
`_emit_loop` paces phone audio at 0.75x realtime, so a 4.4s greeting should ship in ~3.3s - the
18-35s + its variance is unexplained by static code. Two unseparated suspects: (1) emit queue
backing up at startup, (2) ngrok free-tier throttling the media-stream WebSocket.
**No code change to Leo will fix this** (already 0.1s). Next diagnostic (unstarted): instrument
fastrtc `_emit_loop` (queue depth + send timestamps), OR browser A/B via FastRTC's WebRTC test
UI (bypasses Twilio + ngrok) to convict/exonerate ngrok.

### Tooling notes (this session)
- `/tmp/analyze_calls.py` - trace-only call analysis (newest N calls, per-turn total + REP/SLOW
  flags, no per-turn span queries so it dodges Opik's span rate limit). Run inside the container:
  `docker compose cp` it in, then `/app/.venv/bin/python /app/analyze_calls.py 6`.
- Source is bind-mounted (`./src/realtime_phone_agents:/app/...`), so code edits need only
  `docker compose restart phone-calling-agent-api` - no rebuild. `--build` was hanging (compose
  deadlock in `futex_`); the existing image already has all committed code.

---

## Baseline verification (all ✅)

Verified May 29, 2026 in the **local docker** mode (the way Leo is actually run):

| Component | Status | Notes |
|-----------|--------|-------|
| Python env / imports | ✅ | `uv` builds clean, 333 pkgs, no resolution conflict |
| Groq LLM | ✅ | `ChatGroq.invoke`, model `openai/gpt-oss-120b` (set in `.env`; `config.py` default is `20b`) |
| Groq STT | ✅ | `whisper-large-v3` reachable |
| Together AI TTS | ✅ | `/audio/speech`, 8kHz native (Cartesia Sonic 3) |
| Twilio | ✅ | Account active, number still owned |
| Local docker stack | ✅ | Builds + boots, health endpoint green |
| Qdrant (local) + ingest | ✅ | 60 properties from `data/properties.csv` |
| Property search end-to-end | ✅ | Returns relevant Denver listings |
| Opik tracing | ✅ | Workspace `dr-regier`, project `phone-agents` |
| **Live phone call** | ✅ | Booked 2 showings, 2 distinct SMS received |

---

## Operational gotchas (learned this session)

1. **Docker API version mismatch.** The `docker compose` plugin negotiates API v1.52, but
   the daemon on this machine (Docker 20.10.24) only supports up to **1.41**. Plain `docker`
   works; `docker compose` fails with `client version 1.52 is too new`.
   **Fix:** prefix with `DOCKER_API_VERSION=1.41`. The Makefile targets do NOT set this.

2. **Local docker does not auto-ingest.** `make start-call-center` starts an empty local
   Qdrant. You must populate it after the app is healthy.
   Correct local startup sequence:
   ```bash
   DOCKER_API_VERSION=1.41 docker compose up --build -d
   # wait for health (app loads embedding model on boot, ~1-2 min):
   curl http://localhost:8000/health
   # ingest into the local Qdrant:
   curl -X POST http://localhost:8000/superlinked/ingest \
     -H "Content-Type: application/json" \
     -d '{"data_path": "/app/data/properties.csv"}'
   # verify:
   curl -X POST http://localhost:8000/superlinked/search \
     -H "Content-Type: application/json" \
     -d '{"query": "3 bedroom in Denver under 700k", "limit": 2}'
   # for inbound calls:
   make start-ngrok-tunnel   # then point the Twilio number's webhook at the tunnel
   ```
   The `qdrant_data` volume persists across `docker compose down`, so a restart does NOT
   need re-ingest (only `down -v` wipes it).

3. **Two DB modes - don't confuse them.**
   - **Local docker** (what Ryan uses): `docker-compose.yml` overrides `.env` with
     `QDRANT__USE_QDRANT_CLOUD=false`, local Qdrant container. Healthy.
   - **Cloud / gradio** (`make start-gradio-application`, runs on host, reads `.env`,
     `USE_QDRANT_CLOUD=true`): the Qdrant **Cloud** `default` collection is **stale** - its
     index config no longer matches Superlinked 37.5.0 (version drift), so it silently falls
     back to an empty in-memory store and returns no results. Only matters if switching to
     cloud mode; would require recreating the collection (`override_existing`) + re-ingest.

---

## Live-call trace diagnosis (the real work)

Pulled the Opik trace for the test call (thread `MZ902ef17...`, 13 turns). Two issues, both
confirmed from per-stage span timing.

### Issue 1 - Repetition / "hitch in the loop" (the headline bug)

On **turn 12** (caller asked to schedule the 2nd showing but gave no day/time), the model
produced ONE response containing five near-duplicate restatements of the same question:

```
"Got it. What day and time work for you to see the City Park bungalow? Morning or afternoon?
 ...Just let me know what fits your schedule, and I'll lock it in.
 ...Whenever you're ready—just give me a day and time that works for you.
 ...No rush—just hit me with what works and we'll get it on the calendar.
 ...I'm here whenever you're ready—just let me know the day and time..."
```

**Root cause:** `ChatGroq` (`src/realtime_phone_agents/agent/fastrtc_agent.py:144`) is created
with **no `max_tokens` and no `temperature`**. `gpt-oss-120b` degenerated into repetition and
nothing capped it. Secondary: the `?...` joins dodge the sentence splitter
(`_split_sentences`, regex `(?<=[.!?])\s+` needs a space after punctuation), so it all played
as one run-on instead of stopping.

**Knock-on effect:** this same turn had the worst latency - TTS took **11.68s** just to
synthesize all that text. So the repetition and the worst lag are the *same* bug.

### Issue 2 - Latency (3-4s dead air before Leo responds)

Per-stage span timing (dead air ≈ STT + LLM + first-sentence TTS):

| Turn | STT | LLM (ChatGroq) | TTS | Tool | Total |
|------|-----|----------------|-----|------|-------|
| 0 (simple chat) | 2.03s | 0.76s | 4.31s | - | 7.11s |
| 6 (property search) | 2.31s | 0.65s + 0.64s | 0.79s + 4.07s | search 4.00s | 12.56s |
| 12 (the ramble) | 2.20s | 1.51s | **11.68s** | - | 15.51s |

**Findings:**
- **STT is the dominant fixed cost: ~2.0-2.3s on EVERY turn.** The LLM is fast (~0.65s).
  STT is the #1 latency lever for perceived responsiveness.
- TTS (Together/Cartesia, `stream=False`) is ~4s for a normal response; the code already
  sentence-splits and yields per sentence, so first-audio latency = first sentence only.
- The **property search tool takes ~4.0s** (likely re-embedding the query on CPU each call),
  but it sits behind the "let me check" spoken preamble, so it's not dead air. Lower priority.

---

## Plan ahead

### Fix A - Cap the LLM (fixes repetition + worst latency)
> ⚠️ SUPERSEDED by the Jun 1 fix above. The `max_tokens=150` / `temperature=0.3` values below
> were the original guess; the applied fix uses `frequency_penalty` as the primary lever and a
> high `max_tokens=512` backstop (because gpt-oss is a reasoning model). Kept for history.

File: `src/realtime_phone_agents/agent/fastrtc_agent.py:144`
```python
llm = ChatGroq(
    model=settings.groq.model,
    api_key=settings.groq.api_key,
    max_tokens=150,      # hard ceiling so it can't ramble into restatements
    temperature=0.3,     # less likely to degenerate into repetition
)
```
**Open tradeoffs to decide first:**
- `max_tokens=150` might truncate legitimately longer responses (e.g. reading two property
  descriptions back to back). May need a higher cap, or the agent should summarize instead.
- `temperature=0.3` could make Leo sound more robotic, working against the personality the
  recent prompt rewrite added. Tune against that.
- Consider also hardening `_split_sentences` to handle `?...` / `...` joins so a run-on
  can't play as one breath even if the model rambles.

### Fix B - Faster STT (fixes general dead air)
Swap `whisper-large-v3` → `whisper-large-v3-turbo` on Groq (same provider, ~2-4x faster).
Config: `GROQ__STT_MODEL` in `.env` or the default in `config.py` (`GroqSettings.stt_model`).
The STT class (`stt/groq/whisper.py`) already reads `settings.groq.stt_model`, so it's a
config-only change.
**To verify first:** confirm `whisper-large-v3-turbo` is a valid Groq model id and check
accuracy on real call audio (turbo can be slightly less accurate).

### Test loop (the FDE workflow to repeat)
1. Make change on a branch (suggest `fix/latency-and-repetition`).
2. `DOCKER_API_VERSION=1.41 docker compose up --build -d`, wait for health (data persists,
   no re-ingest needed).
3. Re-test the SAME call script (incl. the double-booking that triggered the ramble).
4. Pull traces (`/tmp/pull_traces.py`, `/tmp/pull_spans.py` from this session - or rebuild
   them) and compare before/after per-stage timing. Capture the numbers - good demo material.

### Housekeeping still pending
- **Branch cleanup:** `week4-tts-experiment` is even with `main` (0 commits ahead). Also
  `week3`, `week4`, and a `backup` remote with copies. Consolidate to avoid guessing which
  branch is real. Confirm before deleting.

---

## Bigger picture (the two strategic build targets)

From `ideal-role.md`: the goal is FDE/AI-Engineer fluency, and Five9 Cortex = OpenAI brain +
ElevenLabs voices, with a day-to-day that's ~80% logs/traces/latency debugging.

1. **Latency/trace instrumentation** - exactly what this session did. The spans already exist;
   surfacing a per-stage latency view (or dashboard) would make this repeatable and demo-able.
2. **Un-shelve the OpenAI Realtime version** (`docs/openai-realtime-spec.md`) - mirrors the
   Cortex architecture. Shelved on cost ($2.50/call); reconsider for skill-building with a
   spend cap, since it rehearses the exact stack.
3. **(Optional) ElevenLabs TTS adapter** - matches Cortex's voice layer; the `tts/` layer is
   already pluggable.

The Leo fixes above are the immediate, high-energy on-ramp: a real observed bug, diagnosed
from traces, fixed and re-measured. That's the muscle.
