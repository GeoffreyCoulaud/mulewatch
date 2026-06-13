"""Transport EC async : framing d'UN paquet à la fois sur StreamReader/Writer (spec §4).

Timeout sur CHAQUE lecture réseau (+ l'établissement TCP), configurable (spec §6).
AUCUNE politique ici : pas de retry, pas de reconnexion, pas de sleep — l'adapter
signale, l'appelant décide (spec §3/§6). FCFS strict : une réponse par requête.
"""

import asyncio
import contextlib

from emule_indexer.adapters.mule_ec.codec import (
    EcPacket,
    decode_header,
    decode_payload,
    encode_packet,
)
from emule_indexer.adapters.mule_ec.errors import EcConnectError, EcTimeoutError

_HEADER_SIZE = 8  # réf. §1 (EC_HEADER_SIZE, ECSocket.h:72)


class EcTransport:
    """Encadre l'envoi/la réception d'un paquet EC complet sur une connexion établie."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, timeout: float
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout

    async def send_packet(self, packet: EcPacket) -> None:
        """Émet une trame complète (DÉCISION 5 : pas de timeout d'écriture)."""
        try:
            self._writer.write(encode_packet(packet))
            await self._writer.drain()
        except OSError as exc:
            raise EcConnectError(f"connexion perdue à l'écriture : {exc}") from exc

    async def receive_packet(self) -> EcPacket:
        """Lit EXACTEMENT un paquet : en-tête 8 octets, puis ``length`` octets de payload.

        Après un ``EcTimeoutError``, le flux peut être désynchronisé — jeter le
        transport et en rouvrir un (pas de re-lecture).
        """
        header = await self._read_exactly(_HEADER_SIZE)
        flags, length = decode_header(header)
        payload = await self._read_exactly(length)
        return decode_payload(flags, payload)

    async def close(self) -> None:
        """Ferme la connexion. Nettoyage best-effort : une erreur de socket déjà morte
        est avalée (déviation ASSUMÉE de la lettre de DÉCISION 5 : « signaler » vaut
        pour les opérations, pas pour le cleanup — un OSError brut ici masquerait
        l'erreur d'origine dans un bloc finally)."""
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()

    async def _read_exactly(self, count: int) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.readexactly(count), self._timeout)
        except TimeoutError as exc:
            raise EcTimeoutError("délai de lecture EC dépassé") from exc
        except (asyncio.IncompleteReadError, OSError) as exc:
            raise EcConnectError(f"connexion EC perdue : {exc}") from exc


async def open_ec_transport(host: str, port: int, *, timeout: float) -> EcTransport:
    """Établit la connexion TCP vers ``host:port`` (réf. §0 : port EC par défaut 4712)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except TimeoutError as exc:
        raise EcTimeoutError(f"délai de connexion à {host}:{port} dépassé") from exc
    except OSError as exc:
        raise EcConnectError(f"connexion à {host}:{port} impossible : {exc}") from exc
    return EcTransport(reader, writer, timeout=timeout)
