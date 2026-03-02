"""
Together AI TTS API client.

Uses Together AI's streaming REST API to access TTS models for real-time audio generation.
Default model is Orpheus 3B (canopylabs/orpheus-3b-0.1-ft).

Docs: https://docs.together.ai/docs/text-to-speech

Supported models via Together AI:
- canopylabs/orpheus-3b-0.1-ft (Orpheus 3B) - default
- hexgrad/Kokoro-82M (Kokoro)
- cartesia/sonic-2 (Cartesia Sonic 2) - requires Build Tier 2+
- cartesia/sonic (Cartesia Sonic) - requires Build Tier 2+
"""

import asyncio
import threading
import traceback
from typing import AsyncGenerator, Generator

import httpx
import numpy as np
from loguru import logger
from numpy.typing import NDArray

from realtime_phone_agents.tts.base import TTSModel
from realtime_phone_agents.tts.togetherai.options import (
    DEFAULT_VOICES,
    TogetherTTSOptions,
)


class TogetherTTSModel(TTSModel):
    """
    Client for Together AI's TTS API using streaming REST.

    Sends text to Together AI's TTS API and streams back audio chunks
    in raw PCM format. The audio is returned as 16-bit PCM samples at the
    configured sample rate.

    Audio Flow:
    1. Text sent to Together AI API → API streams raw binary PCM audio
    2. Raw PCM bytes converted to numpy int16 arrays
    3. Audio chunks yielded via generator → ready for FastRTC transmission
    """

    # Audio format constants
    CHANNELS = 1  # Mono audio
    BITS_PER_SAMPLE = 16  # 16-bit PCM
    MIN_CHUNK_SIZE = 1024  # Minimum bytes before yielding a chunk

    def __init__(self, options: TogetherTTSOptions | None = None):
        """
        Initialize the Together AI TTS client.

        Args:
            options: Configuration options for the API. If None, uses defaults.
        """
        self.options = options or TogetherTTSOptions()

        # Validate API key
        if not self.options.api_key:
            raise ValueError(
                "Together AI API key is required. Set TOGETHER__API_KEY environment variable or pass api_key in options."
            )

        # Set voice based on model if not explicitly configured
        if not self.options.voice:
            self.options.voice = DEFAULT_VOICES.get(self.options.model, "tara")

        logger.info(
            f"🎤 Together AI TTS client ready (model: {self.options.model}, voice: {self.options.voice})"
        )

    def set_voice(self, voice: str) -> None:
        """
        Set the voice for TTS generation.

        Args:
            voice: Voice identifier (e.g., 'tara', 'leah', 'jess', 'leo', 'dan', 'mia', 'zac', 'zoe').
        """
        self.options.voice = voice

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for API requests."""
        return {
            "Authorization": f"Bearer {self.options.api_key}",
            "Content-Type": "application/json",
        }

    def _stream_audio_sync(
        self, text: str, options: TogetherTTSOptions
    ) -> Generator[NDArray[np.int16], None, None]:
        """
        Stream audio synchronously from Together AI API.

        Together AI returns raw binary PCM audio when streaming, NOT SSE events.
        The audio is 16-bit PCM (pcm_s16le) at the configured sample rate.

        Args:
            text: Text to convert to speech.
            options: TTS configuration options.

        Yields:
            Audio chunks as numpy arrays of PCM samples (int16).
        """
        speech_url = f"{options.api_url}/audio/speech"

        payload = {
            "model": options.model,
            "input": text.strip(),
            "voice": options.voice,
            "stream": True,
            "response_format": "raw",  # Required for streaming
            "response_encoding": "pcm_s16le",  # 16-bit PCM for clean audio
            "sample_rate": options.sample_rate,
        }

        logger.info(f"📤 Sending {len(text)} chars to Together AI TTS API...")

        chunks_received = 0
        total_bytes = 0
        pcm_buffer = b""

        try:
            with httpx.Client(
                timeout=httpx.Timeout(300.0, connect=10.0),
                headers=self._get_headers(),
            ) as client:
                with client.stream("POST", speech_url, json=payload) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    logger.debug(f"📥 Response content-type: {content_type}")

                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue

                        pcm_buffer += chunk

                        # Send complete 2-byte aligned chunks (int16 = 2 bytes per sample)
                        if len(pcm_buffer) >= self.MIN_CHUNK_SIZE:
                            complete_samples = len(pcm_buffer) // 2
                            if complete_samples > 0:
                                complete_bytes = complete_samples * 2
                                chunks_received += 1
                                total_bytes += complete_bytes

                                audio_chunk = np.frombuffer(
                                    pcm_buffer[:complete_bytes], dtype=np.int16
                                )

                                if chunks_received == 1:
                                    logger.debug(
                                        f"🎵 First audio chunk: {complete_bytes} bytes"
                                    )

                                yield audio_chunk
                                pcm_buffer = pcm_buffer[complete_bytes:]

                    # Flush remaining buffer
                    if pcm_buffer:
                        complete_samples = len(pcm_buffer) // 2
                        if complete_samples > 0:
                            complete_bytes = complete_samples * 2
                            chunks_received += 1
                            total_bytes += complete_bytes

                            audio_chunk = np.frombuffer(
                                pcm_buffer[:complete_bytes], dtype=np.int16
                            )
                            yield audio_chunk

            logger.info(
                f"✅ Together AI TTS completed: {chunks_received} chunks, {total_bytes} bytes"
            )

        except httpx.HTTPStatusError as e:
            error_msg = f"❌ Together AI TTS API error: {e.response.status_code}"
            try:
                error_body = e.response.text
                error_msg += f" - {error_body}"
            except Exception:
                pass
            logger.error(error_msg)
            raise
        except Exception as e:
            logger.error(f"❌ Error generating audio with Together AI TTS: {e}")
            raise

    def stream_tts_sync(
        self,
        text: str,
        options: TogetherTTSOptions | None = None,
    ) -> Generator[tuple[int, NDArray[np.int16]], None, None]:
        """
        Synchronous streaming TTS generation.

        Args:
            text: Text to convert to speech.
            options: Optional TTS configuration.

        Yields:
            Tuples of (sample_rate, audio_chunk).
        """
        opts = options or self.options

        if not text or not text.strip():
            logger.warning("⚠️  Empty text provided to Together AI TTS")
            return

        try:
            for audio_chunk in self._stream_audio_sync(text, opts):
                yield opts.sample_rate, audio_chunk
        except Exception as e:
            logger.error(f"Sync streaming error: {e}")
            traceback.print_exc()

    async def stream_tts(
        self,
        text: str,
        options: TogetherTTSOptions | None = None,
    ) -> AsyncGenerator[tuple[int, NDArray[np.int16]], None]:
        """
        Asynchronous streaming TTS generation.

        This method enables the async for pattern:
            async for sample_rate, chunk in model.stream_tts("Hello"):
                # Process each chunk as it arrives
                await process_audio(chunk, sample_rate)

        Args:
            text: Text to convert to speech.
            options: Optional TTS configuration.

        Yields:
            Tuples of (sample_rate, audio_chunk) as they become available.
        """
        opts = options or self.options

        if not text or not text.strip():
            logger.warning("⚠️  Empty text provided to Together AI TTS")
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[int, NDArray[np.int16]] | None] = asyncio.Queue()

        def worker():
            """Background thread worker that feeds the async queue."""
            try:
                for sample_rate, chunk in self.stream_tts_sync(text, opts):
                    asyncio.run_coroutine_threadsafe(
                        queue.put((sample_rate, chunk)), loop
                    )
            except Exception as e:
                logger.error(f"Worker thread error: {e}")
                traceback.print_exc()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        # Start background worker
        threading.Thread(target=worker, daemon=True).start()

        # Yield chunks as they arrive
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    def tts(
        self,
        text: str,
        options: TogetherTTSOptions | None = None,
    ) -> tuple[int, NDArray[np.int16]]:
        """
        Perform complete text-to-speech conversion (blocking).

        This method waits for the full synthesis to complete and returns
        the complete audio as bytes.

        Args:
            text: Text to convert to speech.
            options: Optional TTS configuration.

        Returns:
            Complete audio as bytes (PCM int16 format).
        """
        opts = options or self.options
        audio_chunks: list[NDArray[np.int16]] = []

        try:
            for sample_rate, chunk in self.stream_tts_sync(text, opts):
                audio_chunks.append(chunk)
        except Exception as e:
            logger.error(f"TTS error: {e}")
            traceback.print_exc()

        if audio_chunks:
            audio = np.concatenate(audio_chunks)
        else:
            audio = np.zeros(0, dtype=np.int16)

        return sample_rate, audio

    async def tts_async(
        self,
        text: str,
        options: TogetherTTSOptions | None = None,
    ) -> bytes:
        """
        Perform complete text-to-speech conversion (async).

        This method waits for the full synthesis to complete and returns
        the complete audio as bytes.

        Args:
            text: Text to convert to speech.
            options: Optional TTS configuration.

        Returns:
            Complete audio as bytes (PCM int16 format).
        """
        opts = options or self.options
        audio_chunks: list[NDArray[np.int16]] = []

        try:
            async for sample_rate, chunk in self.stream_tts(text, opts):
                audio_chunks.append(chunk)
        except Exception as e:
            logger.error(f"TTS async error: {e}")
            traceback.print_exc()

        if audio_chunks:
            audio = np.concatenate(audio_chunks)
        else:
            audio = np.zeros(0, dtype=np.int16)

        return audio.tobytes()

    def tts_blocking(
        self,
        text: str,
        options: TogetherTTSOptions | None = None,
    ) -> tuple[int, NDArray[np.int16]]:
        """
        Synchronous blocking TTS (for non-async contexts).

        Args:
            text: Text to convert to speech.
            options: Optional TTS configuration.

        Returns:
            Tuple of (sample_rate, audio_array).
        """
        opts = options or self.options
        audio_chunks: list[NDArray[np.int16]] = []

        try:
            for sample_rate, chunk in self.stream_tts_sync(text, opts):
                audio_chunks.append(chunk)
        except Exception as e:
            logger.error(f"Blocking TTS error: {e}")
            traceback.print_exc()

        if audio_chunks:
            audio = np.concatenate(audio_chunks)
        else:
            audio = np.zeros(0, dtype=np.int16)

        return opts.sample_rate, audio

    def get_stream_info(self) -> tuple[str, int, int]:
        """
        Get stream format information.

        Returns:
            Tuple of (format, channels, sample_rate)
        """
        return ("pcm_s16le", self.CHANNELS, self.options.sample_rate)
