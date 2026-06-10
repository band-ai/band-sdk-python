"""
Message formatters for converting Band platform messages to LangGraph inputs.

Transforms MessageCreatedPayload (from WebSocket) into the format needed
for your graph's state.
"""

from typing import Dict, Any, Protocol
from band.client.streaming import MessageCreatedPayload


class MessageFormatter(Protocol):
    """Protocol for message formatter functions.

    A message formatter takes a platform message and sender name,
    and returns a dict that matches your graph's state structure.
    """

    def __call__(
        self, message: MessageCreatedPayload, sender_name: str
    ) -> Dict[str, Any]:
        """Convert platform message to graph input.

        Args:
            message: The message payload from WebSocket
            sender_name: Display name of the sender

        Returns:
            Dict matching your graph's state structure
        """
        ...


def default_messages_state_formatter(
    message: MessageCreatedPayload, sender_name: str
) -> Dict[str, Any]:
    """Default formatter for MessagesState (LangGraph standard).

    Formats messages into the standard LangGraph MessagesState format:
    {"messages": [{"role": "user", "content": "..."}]}

    Args:
        message: The message payload from WebSocket
        sender_name: Display name of the sender

    Returns:
        Dict with "messages" key containing a list with one user message

    Example:
        >>> result = default_messages_state_formatter(message, "John Doe")
        >>> result
        {
            "messages": [{
                "role": "user",
                "content": "Message from John Doe (User, ID: abc-123): Hello!"
            }]
        }

    Custom Example:
        >>> def my_formatter(message, sender_name):
        ...     return {
        ...         "content": message.content,
        ...         "sender_name": sender_name,
        ...     }
    """
    formatted_content = (
        f"Message from {sender_name} ({message.sender_type}, ID: {message.sender_id}): "
        f"{message.content}"
    )

    return {"messages": [{"role": "user", "content": formatted_content}]}
