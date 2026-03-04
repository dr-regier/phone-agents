from __future__ import annotations

from langchain.tools import tool
from loguru import logger
from twilio.rest import Client

from realtime_phone_agents.config import settings


def create_send_sms_tool(stream):
    """Factory that returns a send_sms tool bound to the given VoiceAgentStream.

    The tool reads the caller's phone number from ``stream._caller_phone``
    (captured automatically when Twilio posts to the incoming-call webhook)
    so Leo doesn't have to ask the caller for their number.

    Args:
        stream: A ``VoiceAgentStream`` instance whose ``_caller_phone``
                attribute is set during ``handle_incoming_call``.
    """

    @tool
    def send_sms(message: str, phone_number: str | None = None) -> str:
        """Send an SMS text message to the caller.

        Use this tool to text property details, links, or follow-up information
        to the person on the phone. The caller's number is captured automatically
        from the incoming call, but you can override it if they ask you to send
        to a different number.

        Args:
            message: The text content to send. Keep it concise and readable.
                     Use normal digits, dollar signs, and abbreviations — SMS is
                     text, not speech.
            phone_number: Optional override phone number in E.164 format
                          (e.g. +15551234567). If omitted, sends to the caller's
                          number captured from the incoming call.

        Returns:
            Confirmation string with the recipient number, or an error message.
        """
        to_number = phone_number or stream._caller_phone

        if not to_number:
            return (
                "I don't have a phone number to send to. "
                "Could you ask the caller for their number?"
            )

        from_number = settings.twilio.phone_number
        if not from_number:
            logger.error("TWILIO__PHONE_NUMBER is not configured")
            return "SMS is not configured on the server. Please contact support."

        try:
            client = Client(
                settings.twilio.account_sid,
                settings.twilio.auth_token,
            )
            sms = client.messages.create(
                to=to_number,
                from_=from_number,
                body=message,
            )
            logger.info(f"SMS sent to {to_number} — SID: {sms.sid}")
            return f"SMS sent successfully to {to_number}."
        except Exception as e:
            logger.error(f"Failed to send SMS to {to_number}: {e}")
            return f"Sorry, I wasn't able to send that text. Error: {e}"

    return send_sms
