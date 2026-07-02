from emule_indexer.domain.search.cycle import Rng, cycle_seed, shuffle_for_cycle


class _ReverseRng:
    """Deterministic fake Rng: returns the items reversed, ignores the seed (satisfies Rng)."""

    def __init__(self) -> None:
        self.seen_seeds: list[str] = []

    def shuffled(self, items: tuple[str, ...], seed: str) -> tuple[str, ...]:
        self.seen_seeds.append(seed)
        return tuple(reversed(items))

    def jitter(self, span: float) -> float:
        return 0.0


def test_protocol_is_satisfied_structurally() -> None:
    rng: Rng = _ReverseRng()
    assert rng.shuffled(("a", "b"), "seed") == ("b", "a")
    assert rng.jitter(5.0) == 0.0


def test_cycle_seed_combines_node_id_and_index() -> None:
    assert cycle_seed("node-A", 5) == "node-A:5"


def test_shuffle_for_cycle_passes_the_derived_seed_to_the_rng() -> None:
    rng = _ReverseRng()
    shuffle_for_cycle(["x", "y", "z"], rng, "node-A", 7)
    assert rng.seen_seeds == ["node-A:7"]


def test_shuffle_for_cycle_returns_the_rng_permutation() -> None:
    rng = _ReverseRng()
    assert shuffle_for_cycle(["a", "b", "c"], rng, "n", 0) == ("c", "b", "a")


def test_shuffle_for_cycle_does_not_mutate_the_input() -> None:
    rng = _ReverseRng()
    items = ["a", "b", "c"]
    shuffle_for_cycle(items, rng, "n", 0)
    assert items == ["a", "b", "c"]  # the internal tuple protects the caller's sequence
