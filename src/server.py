"""Local WebSocket server the GUI connects to (loopback only)."""
import json
import logging
import threading

from websocket_server import WebsocketServer

from src.constants import PORT

logging.getLogger("websocket_server.websocket_server").disabled = True
log = logging.getLogger("vry.server")


class Server:
    def __init__(self, host="127.0.0.1", port=PORT):
        self.host, self.port = host, port
        self._server = None
        self._last = {}
        self._lock = threading.Lock()

    def start(self):
        self._server = WebsocketServer(host=self.host, port=self.port)
        self.port = self._server.server_address[1]  # resolves port 0
        self._server.set_fn_new_client(self._on_client)
        self._server.run_forever(threaded=True)
        log.info("GUI server listening on %s:%s", self.host, self.port)

    def stop(self):
        if self._server:
            self._server.shutdown_gracefully()

    def _on_client(self, client, server):
        # A GUI that connects late still gets the current board.
        with self._lock:
            messages = list(self._last.values())
        for message in messages:
            server.send_message(client, message)

    def send_payload(self, kind, payload):
        message = json.dumps({**payload, "type": kind})
        with self._lock:
            self._last[kind] = message
        if self._server:
            try:
                self._server.send_message_to_all(message)
            except Exception:
                log.exception("failed to broadcast %s", kind)
