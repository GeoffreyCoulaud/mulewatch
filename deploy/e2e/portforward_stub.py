"""Stub HTTP du control-server gluetun (spec e2e §5.5) — outil de test SEUL.

Sert ``GET /v1/portforward → {"port": N}`` (la route que la boucle port-sync lit). Zéro
WireGuard, zéro auth. ``N`` vient de la variable d'environnement ``FORWARDED_PORT`` (défaut
``16500``). Tourne dans un ``python:3.12-slim`` du compose e2e ; n'importe RIEN d'``emule_indexer``.

Le sous-test port-sync (skippable) : la boucle port-sync lit ``N`` → EC ``SetPort(N)`` + restart
amuled → on observe le HighID accordé par ed2kd (port-check entrant sur ``N``) ⇒ port-sync correct.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_PORT = int(os.environ.get("LISTEN_PORT", "8000"))
_FORWARDED_PORT = int(os.environ.get("FORWARDED_PORT", "16500"))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - signature imposée par http.server
        if self.path == "/v1/portforward":
            body = json.dumps({"port": _FORWARDED_PORT}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args: object) -> None:
        return None


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
