"""Analyzer config (analysis spec §8 — DA10).

``AnalysisConfig`` (frozen) read from the environment by ``from_env``: enabled checks, ffprobe
path, timeout, rlimits, egress cap, sniffed-header size, quarantine directory. The PARENT
(service) uses it for the spawn's rlimits/timeout/minimal env; the CHILD re-reads the "checks"
part (``enabled_checks``/``ffprobe_path``/``header_bytes``) from the minimal env the parent passes
it. Defaults are tunable; invalid values → ``ValueError`` (fail-fast at service startup). Crawler
side: no new config.
"""

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_ENABLED = ("type_sniff", "ffprobe")
_DEFAULT_QUARANTINE = "/quarantine"

# Closed enum of implemented checks (must stay aligned with pipeline.run's dispatch). A name
# outside this set is a typo: rejecting it at load time prevents a silent fail-open (zero checks
# run → 'clean' verdict for every file).
KNOWN_CHECKS = frozenset({"type_sniff", "ffprobe", "clamav"})


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Frozen analyzer parameters (a single object shared parent/child via the env)."""

    enabled_checks: tuple[str, ...]
    ffprobe_path: str
    clamscan_path: str
    clamav_db_dir: str
    timeout_s: float
    rlimit_cpu_s: int
    rlimit_as_bytes: int
    rlimit_nproc: int
    rlimit_nofile: int
    rlimit_fsize_bytes: int
    egress_cap_bytes: int
    header_bytes: int
    quarantine_dir: str
    seccomp_enabled: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "AnalysisConfig":
        """Build the config from ``env``. Unparsable value / empty list → ``ValueError``."""
        enabled = _parse_checks(env.get("ENABLED_CHECKS"))
        # clamav loads its whole signature database (hundreds of MB): when it is enabled, we
        # conditionally relax the child's memory/CPU caps (§6.2). An explicit override
        # (RLIMIT_AS_BYTES / RLIMIT_CPU_S, non-suffixed) ALWAYS wins over this conditional default.
        clamav_on = "clamav" in enabled
        as_default = (
            _parse_int(env.get("RLIMIT_AS_BYTES_CLAMAV"), 1536 * 1024 * 1024)
            if clamav_on
            else 512 * 1024 * 1024
        )
        cpu_default = _parse_int(env.get("RLIMIT_CPU_S_CLAMAV"), 120) if clamav_on else 20
        # The wall-clock timeout (communicate) MUST cover the CPU budget: with clamav (CPU 120 s),
        # a timeout pinned at 30 s would kill the scan of a slow healthy media → false positive
        # (sandbox-confinement#1). So we relax the default conditionally, like the rlimits.
        timeout_default = (
            _parse_float(env.get("ANALYSIS_TIMEOUT_S_CLAMAV"), 150.0) if clamav_on else 30.0
        )
        config = cls(
            enabled_checks=enabled,
            ffprobe_path=env.get("FFPROBE_PATH", "ffprobe"),
            clamscan_path=env.get("CLAMSCAN_PATH", "clamscan"),
            clamav_db_dir=env.get("CLAMAV_DB_DIR", "/clamav-db"),
            timeout_s=_parse_float(env.get("ANALYSIS_TIMEOUT_S"), timeout_default),
            rlimit_cpu_s=_parse_int(env.get("RLIMIT_CPU_S"), cpu_default),
            rlimit_as_bytes=_parse_int(env.get("RLIMIT_AS_BYTES"), as_default),
            # RLIMIT_NPROC is PER-UID GLOBAL (not per subtree): 64 is sane ONLY because the
            # verifier runs on a dedicated, lightly-populated UID (Docker image, Plan F). On
            # bare-metal dev/CI where the UID already has >64 processes, override RLIMIT_NPROC
            # (otherwise fork is refused).
            rlimit_nproc=_parse_int(env.get("RLIMIT_NPROC"), 64),
            rlimit_nofile=_parse_int(env.get("RLIMIT_NOFILE"), 64),
            rlimit_fsize_bytes=_parse_int(env.get("RLIMIT_FSIZE_BYTES"), 16 * 1024 * 1024),
            egress_cap_bytes=_parse_int(env.get("EGRESS_CAP_BYTES"), 65536),
            header_bytes=_parse_int(env.get("HEADER_BYTES"), 4096),
            quarantine_dir=env.get("QUARANTINE_DIR", _DEFAULT_QUARANTINE),
            # kernel ring (seccomp) ON by default: in prod the container sets no_new_privs (§3).
            seccomp_enabled=_parse_bool(env.get("SECCOMP_ENABLED"), True),
        )
        _validate_positive(config)
        return config


def _validate_positive(config: AnalysisConfig) -> None:
    """Fail-fast floor: no security-critical limit may be <= 0 (config-validation#3).

    An ``rlimit_*`` <= 0 disarms the guard (``-1`` == ``RLIM_INFINITY``, ``0`` prevents exec);
    ``timeout_s``/``egress_cap_bytes``/``header_bytes`` <= 0 break the analysis or force it to
    ``suspicious``. We reject at load time, naming the offending environment variable.
    """
    bounds: tuple[tuple[str, float], ...] = (
        ("ANALYSIS_TIMEOUT_S", config.timeout_s),
        ("RLIMIT_CPU_S", config.rlimit_cpu_s),
        ("RLIMIT_AS_BYTES", config.rlimit_as_bytes),
        ("RLIMIT_NPROC", config.rlimit_nproc),
        ("RLIMIT_NOFILE", config.rlimit_nofile),
        ("RLIMIT_FSIZE_BYTES", config.rlimit_fsize_bytes),
        ("EGRESS_CAP_BYTES", config.egress_cap_bytes),
        ("HEADER_BYTES", config.header_bytes),
    )
    for name, value in bounds:
        if value <= 0:
            raise ValueError(f"{name} must be > 0, got {value}")


def _parse_checks(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_ENABLED
    checks = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not checks:
        raise ValueError("ENABLED_CHECKS must not be empty")
    unknown = [name for name in checks if name not in KNOWN_CHECKS]
    if unknown:
        raise ValueError(
            f"ENABLED_CHECKS contains unknown checks {unknown} (known: {sorted(KNOWN_CHECKS)})"
        )
    return checks


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"expected an integer, got {raw!r}") from exc


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"expected a float, got {raw!r}") from exc


_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")


def _parse_bool(raw: str | None, default: bool) -> bool:
    # Case-insensitive + whitespace-tolerant (config-validation#5): `True`, `TRUE`, `On`, ` true `
    # are equivalent to `true`. The error message LISTS the accepted literals to help the operator
    # fix it.
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _BOOL_FALSE:
        return False
    if normalized in _BOOL_TRUE:
        return True
    accepted = ", ".join((*_BOOL_TRUE, *_BOOL_FALSE))
    raise ValueError(f"expected a boolean (accepted: {accepted}), got {raw!r}")
