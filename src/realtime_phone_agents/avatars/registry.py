"""Avatar registry and management functions."""

from typing import Dict, List
from realtime_phone_agents.avatars.base import Avatar


# Leo is defined directly — no YAML loading needed.
# To revert to YAML-based loading, uncomment from_yaml in base.py
# and restore the _load_avatars() method below.
LEO = Avatar(
    name="Leo",
    description="Friendly and knowledgeable Denver real estate expert who gives great advice",
)


class AvatarRegistry:
    """Central registry for managing avatars."""

    def __init__(self):
        self._avatars: Dict[str, Avatar] = {}

    def register(self, avatar: Avatar) -> None:
        self._avatars[avatar.id] = avatar

    def get(self, avatar_id: str) -> Avatar:
        avatar_id_lower = avatar_id.lower()
        if avatar_id_lower not in self._avatars:
            available = ", ".join(self._avatars.keys())
            raise ValueError(
                f"Avatar '{avatar_id}' not found. Available avatars: {available}"
            )
        return self._avatars[avatar_id_lower]

    def list_all(self) -> Dict[str, str]:
        return {
            avatar_id: avatar.description
            for avatar_id, avatar in self._avatars.items()
        }

    def get_all(self) -> List[Avatar]:
        return list(self._avatars.values())

    @property
    def available_ids(self) -> List[str]:
        return list(self._avatars.keys())


# Global registry instance
_registry = AvatarRegistry()


def get_avatar(avatar_id: str) -> Avatar:
    return _registry.get(avatar_id)


def list_avatars() -> Dict[str, str]:
    return _registry.list_all()


def get_all_avatars() -> List[Avatar]:
    return _registry.get_all()


def register_avatar(avatar: Avatar) -> None:
    _registry.register(avatar)


def register_all_avatars() -> None:
    """Register Leo (and any future avatars)."""
    register_avatar(LEO)


def version_all_avatars() -> None:
    """Version all registered avatars for Opik tracking."""
    for avatar in _registry.get_all():
        avatar.version_system_prompt()


# --- YAML-based loading (commented out — keeping for future multi-avatar support) ---
# from pathlib import Path
#
# def _load_avatars_from_yaml(definitions_dir: Path | None = None):
#     if definitions_dir is None:
#         definitions_dir = Path(__file__).parent / "definitions"
#     if not definitions_dir.exists():
#         raise FileNotFoundError(f"Avatar definitions directory not found: {definitions_dir}")
#     yaml_files = list(definitions_dir.glob("*.yaml")) + list(definitions_dir.glob("*.yml"))
#     for yaml_file in yaml_files:
#         avatar = Avatar.from_yaml(yaml_file)
#         register_avatar(avatar)
