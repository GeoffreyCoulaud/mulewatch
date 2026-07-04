"""``CatalogRepository`` port: the catalog's durable memory (spec data-model §4).

SYNCHRONOUS Protocol (spec §3: a local write is sub-millisecond; if plan C wants to
isolate itself, it will wrap it in ``asyncio.to_thread`` without touching this layer).
The port imports ONLY the domain. Stubs fit on ONE line (the ``def`` runs at class
creation: covered). The adapter stamps ``observed_at``/``decided_at``/``node_id`` — that
is why ``record_decision`` receives the hash ALONGSIDE the decision (``MatchDecision`` does
not carry the content key, by principle: a domain without persistence columns).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from catalog_matching.engine import DecisionRecord, DownloadCandidate, MatchDecision
from emule_indexer.domain.observation import FileObservation


@dataclass(frozen=True)
class ObservedFile:
    """Minimal READ shape of an observation: name + size (to build an ed2k link).

    The download loop (spec §5) reads the LATEST observation of a hash to rebuild its ed2k
    link (``build_ed2k_link(filename, size_bytes, hash)``). We return only the two required
    fields — not the whole ``FileObservation`` (the rest is useless for download).
    """

    filename: str
    size_bytes: int


class CatalogRepository(Protocol):
    """Sync catalog write contract (append-only; the adapter reports, it does not decide).

    ``last_decision`` (anti-redundancy, spec orchestration §3) returns a :class:`DecisionRecord`.
    ``download_decisions`` (spec download §5) returns the :class:`DownloadCandidate` whose
    LATEST verdict is tier=download (to be replayed by the download loop). ``last_observation``
    returns the most recent :class:`ObservedFile` of a hash (name+size for the ed2k link), or
    ``None``. These three reads are harmless (no write).
    ``record_verification`` (spec verify §5) appends a ``file_verifications`` row (append-only
    catalog, tagged ``node_id``) — the verdict decision is made elsewhere (the verifier), the
    adapter only persists.
    ``record_retraction`` (spec re-evaluation §5) appends a sentinel ``match_decisions`` row
    (``target_id=""``, ``rule_name=""``, ``tier="retracted"``) marking a previously-matched
    file as no longer matching any target — the append-only table has no "delete", so
    exclusion is represented as an appended row instead.
    """

    def record_observation(self, observation: FileObservation) -> None: ...

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None: ...

    def record_retraction(self, ed2k_hash: str) -> None: ...

    def last_decision(self, ed2k_hash: str) -> DecisionRecord | None: ...

    def download_decisions(self) -> tuple[DownloadCandidate, ...]: ...

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None: ...

    def record_verification(
        self,
        ed2k_hash: str,
        verdict: str,
        real_meta: Mapping[str, object],
        checks: Sequence[object],
    ) -> None: ...
