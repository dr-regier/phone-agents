import json

from langchain.tools import tool

from realtime_phone_agents.infrastructure.superlinked.service import (
    get_property_search_service,
)


@tool
def search_property_mock_tool(location: str) -> str:
    """Retrieve real estate details for properties in a given location."""
    return (
        "I found one apartment in that area. It features 3 rooms, "
        "2 bathrooms, and a beautifully designed living room."
    )


@tool
async def search_property_tool(query: str, limit: int = 1) -> str:
    """Semantic search over the property database by natural-language query.

    Args:
        query: NL description of requirements (location, price, beds/baths, type, features).
        limit: Max properties to return (default 1; raise to compare options).

    Returns:
        JSON of matching properties (address, price, beds, baths, sqft, features),
        or a not-found message.
    """
    property_search_service = get_property_search_service()
    properties = await property_search_service.search_properties(query, limit)

    if not properties:
        return "No properties found matching the criteria."

    return json.dumps(properties, indent=2)
