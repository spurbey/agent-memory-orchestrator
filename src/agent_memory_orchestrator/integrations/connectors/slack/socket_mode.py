from __future__ import annotations

import json
import time
from typing import Any

from .client import SlackApiClient
from .events import parse_message_envelope
from .service import SlackConnectorError, SlackConnectorService


class SlackSocketModeRunner:
    """Minimal local Socket Mode runner.

    Slack connects to this process through an outbound WebSocket, so AMO stays
    local and no public webhook URL is required.
    """

    def __init__(self, service: SlackConnectorService, *, reply_mode: str = "answer") -> None:
        if reply_mode not in {"disabled", "ack", "answer"}:
            raise ValueError("reply_mode must be one of: disabled, ack, answer")
        self.service = service
        self.reply_mode = reply_mode

    def run_forever(self) -> None:
        websocket = _load_websocket_module()
        client = SlackApiClient(
            app_token=self.service.config.app_token,
            bot_token=self.service.config.bot_token,
        )
        url = client.open_socket()
        ws = websocket.WebSocket()
        ws.connect(url, timeout=30)
        try:
            while True:
                raw = ws.recv()
                if not raw:
                    time.sleep(0.1)
                    continue
                envelope = json.loads(raw)
                if not isinstance(envelope, dict):
                    continue
                envelope_id = str(envelope.get("envelope_id") or "")
                if envelope_id:
                    ws.send(json.dumps({"envelope_id": envelope_id}))
                result = self.service.handle_event_envelope(envelope)
                if self.reply_mode != "disabled" and result.get("reply_required"):
                    message = parse_message_envelope(envelope)
                    if message is not None:
                        if self.reply_mode == "ack":
                            self.service.post_ack_reply(channel=message.channel_id, thread_ts=message.thread_ts or message.ts)
                        else:
                            self.service.post_answer_reply(message=message)
        finally:
            ws.close()


def _load_websocket_module() -> Any:
    try:
        import websocket  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SlackConnectorError(
            "websocket-client is required for Slack Socket Mode. "
            "Install with: pip install -e \".[slack]\""
        ) from exc
    return websocket
