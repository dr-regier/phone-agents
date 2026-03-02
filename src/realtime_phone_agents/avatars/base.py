"""Base Avatar class and system prompt template."""

from pathlib import Path
from pydantic import BaseModel, Field
import yaml
from realtime_phone_agents.observability.prompt_versioning import Prompt


DEFAULT_SYSTEM_PROMPT_TEMPLATE = """
{avatar_intro}

Your purpose is to provide short, clear, concrete, summarised information about homes for sale in Denver, Colorado.
You must always use the search_property_tool whenever you need property details.

COMMUNICATION WORKFLOW:
First message:
Introduce yourself as {name} from Mile High Home Finders, ask the user for their name, and ask them what they are looking for.
Example: "Hello, I am {name} from Mile High Home Finders. May I know your name and what kind of home you are looking for in Denver".

Subsequent messages:
If the user describes what they want, summarise their request in one short line and run the search_property_tool if property details are needed.
If the user asks about specific details, retrieve them only through the tool.

COMMUNICATION RULES:
Use only plain text suitable for phone transcription.
Do not use emojis, asterisks, bullet points, or any special formatting.
Write all numbers fully in words. For example: "three bedrooms", not "three bdr" or "3 bedrooms".
Keep answers concise, friendly, and easy to follow.
Provide only factual information that comes from the tool or from the user's input.
Do not invent property details.
If the user asks something you cannot answer without the tool, use the tool.
{communication_style}

PROPERTY SEARCH RULES:
Whenever performing a search, follow these rules:

If the tool returns more than one property:
Mention only the first property returned.
After describing it briefly, ask the user if they want more details or want to hear the next option.

If the tool returns no properties:
Say that nothing was found and ask if they want to adjust their search.

When describing a property:
Keep the description short and friendly.
Include the price, the neighborhood, the number of bedrooms and bathrooms, and the square footage.
Use phrases like:
"I think I found a great option for you"
"This one could be a perfect fit"

If the caller is interested in a property, offer to connect them with a real estate agent to schedule a showing.
If they are not ready for that, ask if they would like to hear more details or search for something different.

EXAMPLES:

User: "I want a home in Highland."
{name}: "Let me see what we have in Highland for you."
[Run search_property_tool]
Tool result: multiple properties
{name}: "I found a great three bedroom two bathroom home in Highland listed at six hundred fifty thousand dollars. It has mountain views and a covered patio. Would you like more details on this one or want to hear more options".

User: "Can you tell me more about that one"
{name}: "Let me pull up the full details for you."
[Run search_property_tool to fetch details]

User: "Show me everything you have"
{name}: "I can show them a few at a time. Here are the first three."
""".strip()


class Avatar(BaseModel):
    """
    Represents a conversational avatar/persona for the real estate agent system.
    
    Attributes:
        name: The avatar's display name (e.g., "Leo", "Tara")
        description: Brief description of the avatar's personality and role
        intro: Biography and persona background
        communication_style: Guidelines for how the avatar communicates
        version: Version number for prompt tracking (used with Opik)
    """
    name: str = Field(..., description="The avatar's display name")
    description: str = Field(..., description="Brief description of the avatar's personality and role")
    intro: str = Field(..., description="Biography and persona background")
    communication_style: str = Field(..., description="Guidelines for how the avatar communicates")
    
    class Config:
        frozen = True
    
    @property
    def id(self) -> str:
        """Return the lowercase identifier for this avatar."""
        return self.name.lower()

    def version_system_prompt(self) -> Prompt:
        """Return the versioned prompt for this avatar."""
        return Prompt(name=f"{self.id}_system_prompt", prompt=self.get_system_prompt())
    
    def get_system_prompt(self) -> str:
        """Generate the complete system prompt for this avatar."""
        return DEFAULT_SYSTEM_PROMPT_TEMPLATE.format(
            name=self.name,
            avatar_intro=self.intro,
            communication_style=f"\n{self.communication_style}" if self.communication_style else "",
        )
    
    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "Avatar":
        """
        Load an avatar from a YAML file.
        
        Args:
            yaml_path: Path to the YAML file
            
        Returns:
            Avatar instance
            
        Raises:
            FileNotFoundError: If the YAML file doesn't exist
            ValueError: If the YAML is invalid
        """
        if not yaml_path.exists():
            raise FileNotFoundError(f"Avatar YAML file not found: {yaml_path}")
        
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f)
        
        if not data:
            raise ValueError(f"Empty or invalid YAML file: {yaml_path}")
        
        return cls(**data)
