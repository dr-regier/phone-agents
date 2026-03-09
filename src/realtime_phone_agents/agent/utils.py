def get_tool_call_names(model_step_data) -> list[str]:
    """Return the names of all tool calls in this model step."""
    names = []
    msgs = None
    if isinstance(model_step_data, dict) and "messages" in model_step_data:
        msgs = model_step_data["messages"]
    elif isinstance(model_step_data, list):
        msgs = model_step_data
    else:
        msgs = [model_step_data]

    for msg in msgs:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    names.append(name)

        if isinstance(msg, dict) and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    names.append(name)

    return names


def model_has_tool_calls(model_step_data) -> bool:
    """
    Heuristic: returns True if this 'model' step contains tool_calls.
    The exact schema depends on your agent; adjust as needed.
    """
    msgs = None
    if isinstance(model_step_data, dict) and "messages" in model_step_data:
        msgs = model_step_data["messages"]
    elif isinstance(model_step_data, list):
        msgs = model_step_data
    else:
        msgs = [model_step_data]

    for msg in msgs:
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            return True

        if isinstance(msg, dict) and msg.get("tool_calls"):
            return True

        content = getattr(msg, "content", None) or (
            msg.get("content") if isinstance(msg, dict) else None
        )
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("tool_calls"):
                    return True

    return False
