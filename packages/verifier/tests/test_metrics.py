"""VerifierMetrics : compteur par verdict + histogramme de durée, sur un registre dédié."""

from download_verifier.metrics import VerifierMetrics


def test_observe_increments_counter_and_histogram() -> None:
    metrics = VerifierMetrics()
    metrics.observe("clean", 0.5)
    metrics.observe("clean", 0.7)
    metrics.observe("malicious", 0.1)
    registry = metrics.registry
    assert registry.get_sample_value("emule_verifier_requests_total", {"verdict": "clean"}) == 2.0
    assert (
        registry.get_sample_value("emule_verifier_requests_total", {"verdict": "malicious"}) == 1.0
    )
    assert registry.get_sample_value("emule_verifier_analysis_duration_seconds_count") == 3.0
