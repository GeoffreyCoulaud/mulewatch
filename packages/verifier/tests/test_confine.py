from download_verifier.confine import NoopConfiner, ProdConfiner


def test_noop_confiner_does_nothing() -> None:
    # the NoopConfiner installs NO filter: __call__ returns None (real line covered).
    assert NoopConfiner()() is None


def test_prod_confiner_constructs() -> None:
    # the constructor is not pragma; __call__ (real seccomp) is.
    assert isinstance(ProdConfiner(), ProdConfiner)
