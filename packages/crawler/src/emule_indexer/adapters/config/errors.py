"""Erreur de config commune (fail-fast §5/§14), isolée pour casser un cycle d'import."""


class ConfigError(Exception):
    """Config invalide → refus de démarrer (fail-fast, spec §5/§14)."""
