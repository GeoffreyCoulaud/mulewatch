"""Verifier technical metrics (E-D10). No events/notifications (crawler-only):

- ``emule_verifier_requests{verdict}``: historical counter of business verdicts returned by
  ``/verify`` (clean/suspicious/malicious/error).
- ``emule_verifier_child_outcome{outcome}`` (observability#2): TECHNICAL cause of the child's
  outcome (ok/timeout/nonzero_exit/egress_overflow/malformed) — orthogonal to the verdict, lets
  you identify in a mass incident the cause behind a rise in ``suspicious``.
- ``emule_verifier_responses{status}`` (observability#3): counter of HTTP responses by code
  (200/400/500) — covers the 400s (validation) and 500s (exceptions) that ``observe`` did not see.
- ``emule_verifier_analysis_duration_seconds``: analysis-duration histogram.

Counter WITHOUT ``_total`` (added by prometheus_client at exposition time).
"""

from prometheus_client import CollectorRegistry, Counter, Histogram


class VerifierMetrics:
    """Registry + verdict / child_outcome / responses counters + duration histogram."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._requests = Counter(
            "emule_verifier_requests",
            "/verify requests processed",
            ["verdict"],
            registry=self.registry,
        )
        self._child_outcome = Counter(
            "emule_verifier_child_outcome",
            "Technical cause of the analysis child's outcome",
            ["outcome"],
            registry=self.registry,
        )
        self._responses = Counter(
            "emule_verifier_responses",
            "Verifier HTTP responses",
            ["status"],
            registry=self.registry,
        )
        self._duration = Histogram(
            "emule_verifier_analysis_duration_seconds",
            "File analysis duration (s)",
            registry=self.registry,
        )

    def observe(self, verdict: str, seconds: float) -> None:
        """Count a request (by verdict) and observe its analysis duration."""
        self._requests.labels(verdict=verdict).inc()
        self._duration.observe(seconds)

    def observe_child_outcome(self, outcome: str) -> None:
        """Count the TECHNICAL cause of the child's outcome (observability#2)."""
        self._child_outcome.labels(outcome=outcome).inc()

    def observe_response(self, status: int) -> None:
        """Count an HTTP response by status code (observability#3)."""
        self._responses.labels(status=str(status)).inc()
