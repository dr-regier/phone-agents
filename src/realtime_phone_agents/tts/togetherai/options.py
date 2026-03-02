"""Together AI TTS API options."""

from pydantic import BaseModel, Field

from realtime_phone_agents.config import settings

# Default voices per model
DEFAULT_VOICES = {
    "canopylabs/orpheus-3b-0.1-ft": "tara",
    "hexgrad/Kokoro-82M": "af_heart",
    "cartesia/sonic-3": "customer support man",
    "cartesia/sonic-2": "sarah",
    "cartesia/sonic": "sarah",
}


class TogetherTTSOptions(BaseModel):
    """Configuration options for Together AI TTS API.

    Supported models via Together AI:
        - canopylabs/orpheus-3b-0.1-ft (Orpheus 3B) - default
        - hexgrad/Kokoro-82M (Kokoro)
        - cartesia/sonic-2 (Cartesia Sonic 2)
        - cartesia/sonic (Cartesia Sonic)
    """

    api_key: str = Field(
        default_factory=lambda: settings.together.api_key,
        description="Together AI API Key",
    )
    api_url: str = Field(
        default_factory=lambda: settings.together.api_url,
        description="Together AI API URL",
    )
    model: str = Field(
        default_factory=lambda: settings.together.model,
        description="Together AI TTS Model (e.g., canopylabs/orpheus-3b-0.1-ft)",
    )
    voice: str = Field(
        default_factory=lambda: settings.together.voice,
        description="Voice to use for synthesis",
    )
    sample_rate: int = Field(
        default_factory=lambda: settings.together.sample_rate,
        description="Audio sample rate (Hz)",
    )

    model_config = {"arbitrary_types_allowed": True}
