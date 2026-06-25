"""Config de l'analyseur (spec analysis §8 — DA10).

``AnalysisConfig`` (frozen) lue depuis l'environnement par ``from_env`` : checks activés,
chemin ffprobe, timeout, rlimits, cap d'égress, taille d'en-tête sniffée, dossier de
quarantaine. Le PARENT (service) l'utilise pour les rlimits/timeout/env minimal du spawn ;
l'ENFANT relit la part « checks » (``enabled_checks``/``ffprobe_path``/``header_bytes``) depuis
l'env minimal que le parent lui passe. Défauts raffinables ; valeurs invalides → ``ValueError``
(fail-fast au démarrage du service). Côté crawler : aucune config nouvelle.
"""

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_ENABLED = ("type_sniff", "ffprobe")
_DEFAULT_QUARANTINE = "/quarantine"

# Enum fermé des checks implémentés (doit rester aligné sur le dispatch de pipeline.run).
# Un nom hors de cet ensemble est une faute de frappe : la rejeter au chargement empêche un
# fail-open silencieux (zéro check exécuté → verdict 'clean' pour tout fichier).
KNOWN_CHECKS = frozenset({"type_sniff", "ffprobe", "clamav"})


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Paramètres figés de l'analyseur (un seul objet partagé parent/enfant via l'env)."""

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
        """Construit la config depuis ``env``. Valeur non parsable / liste vide → ``ValueError``."""
        enabled = _parse_checks(env.get("ENABLED_CHECKS"))
        # clamav charge toute la base de signatures (centaines de Mo) : quand il est activé, on
        # relâche conditionnellement les plafonds mémoire/CPU du child (§6.2). Un override explicite
        # (RLIMIT_AS_BYTES / RLIMIT_CPU_S, non suffixé) prime TOUJOURS sur ce défaut conditionnel.
        clamav_on = "clamav" in enabled
        as_default = (
            _parse_int(env.get("RLIMIT_AS_BYTES_CLAMAV"), 1536 * 1024 * 1024)
            if clamav_on
            else 512 * 1024 * 1024
        )
        cpu_default = _parse_int(env.get("RLIMIT_CPU_S_CLAMAV"), 120) if clamav_on else 20
        # Le timeout wall-clock (communicate) DOIT couvrir le budget CPU : avec clamav (CPU 120 s),
        # un timeout figé à 30 s tuerait le scan d'un média sain lent → faux positif (sandbox-
        # confinement#1). On relâche donc le défaut conditionnellement, comme les rlimits.
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
            # RLIMIT_NPROC est PAR-UID GLOBAL (pas par sous-arbre) : 64 est sain UNIQUEMENT parce
            # que le verifier tourne sur un UID dédié peu peuplé (image Docker, Plan F). En dev/CI
            # bare-metal où l'UID a déjà >64 process, overrider RLIMIT_NPROC (sinon fork refusé).
            rlimit_nproc=_parse_int(env.get("RLIMIT_NPROC"), 64),
            rlimit_nofile=_parse_int(env.get("RLIMIT_NOFILE"), 64),
            rlimit_fsize_bytes=_parse_int(env.get("RLIMIT_FSIZE_BYTES"), 16 * 1024 * 1024),
            egress_cap_bytes=_parse_int(env.get("EGRESS_CAP_BYTES"), 65536),
            header_bytes=_parse_int(env.get("HEADER_BYTES"), 4096),
            quarantine_dir=env.get("QUARANTINE_DIR", _DEFAULT_QUARANTINE),
            # ring noyau (seccomp) ON par défaut : en prod le conteneur pose no_new_privs (§3).
            seccomp_enabled=_parse_bool(env.get("SECCOMP_ENABLED"), True),
        )
        _validate_positive(config)
        return config


def _validate_positive(config: AnalysisConfig) -> None:
    """Plancher fail-fast : aucune limite sécurité-critique ne doit être <= 0 (config-validation#3).

    Un ``rlimit_*`` <= 0 désarme la garde (``-1`` == ``RLIM_INFINITY``, ``0`` empêche l'exec) ;
    ``timeout_s``/``egress_cap_bytes``/``header_bytes`` <= 0 cassent l'analyse ou la forcent en
    ``suspicious``. On rejette au chargement en nommant la variable d'environnement fautive.
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
            raise ValueError(f"{name} doit être > 0, reçu {value}")


def _parse_checks(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_ENABLED
    checks = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not checks:
        raise ValueError("ENABLED_CHECKS ne doit pas être vide")
    unknown = [name for name in checks if name not in KNOWN_CHECKS]
    if unknown:
        raise ValueError(
            f"ENABLED_CHECKS contient des checks inconnus {unknown} "
            f"(connus : {sorted(KNOWN_CHECKS)})"
        )
    return checks


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"entier attendu, reçu {raw!r}") from exc


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"flottant attendu, reçu {raw!r}") from exc


_BOOL_TRUE = ("1", "true", "yes", "on")
_BOOL_FALSE = ("0", "false", "no", "off")


def _parse_bool(raw: str | None, default: bool) -> bool:
    # Insensible à la casse + tolère le whitespace (config-validation#5) : `True`, `TRUE`,
    # `On`, ` true ` sont équivalents à `true`. Le message d'erreur LISTE les littéraux
    # acceptés pour aider l'opérateur à corriger.
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _BOOL_FALSE:
        return False
    if normalized in _BOOL_TRUE:
        return True
    accepted = ", ".join((*_BOOL_TRUE, *_BOOL_FALSE))
    raise ValueError(f"booléen attendu (accepté : {accepted}), reçu {raw!r}")
