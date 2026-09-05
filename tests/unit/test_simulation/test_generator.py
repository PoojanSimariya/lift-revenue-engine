"""Unit tests for the deterministic synthetic transaction and customer generator."""

from __future__ import annotations

import json

from lift.simulation.generator import SyntheticBatchGenerator


def test_seed_reproducibility_seed_42() -> None:
    """For the supported Python/runtime/dependency versions,
    identical seeds produce identical serialized synthetic batches.
    """
    gen1 = SyntheticBatchGenerator(seed=42)
    batch1 = gen1.generate_batch(count=50)
    serialized1 = SyntheticBatchGenerator.serialize_batch(batch1)

    gen2 = SyntheticBatchGenerator(seed=42)
    batch2 = gen2.generate_batch(count=50)
    serialized2 = SyntheticBatchGenerator.serialize_batch(batch2)

    # Assert exact serialized JSON equality
    json1 = json.dumps(serialized1, sort_keys=True)
    json2 = json.dumps(serialized2, sort_keys=True)
    assert json1 == json2, "Identical seed 42 must produce 100% identical serialized batches."


def test_seed_reproducibility_seed_2026_and_divergence() -> None:
    """Different seeds produce distinct, self-consistent reproducible distributions."""
    gen_2026_a = SyntheticBatchGenerator(seed=2026)
    batch_2026_a = gen_2026_a.generate_batch(count=50)
    serialized_a = SyntheticBatchGenerator.serialize_batch(batch_2026_a)

    gen_2026_b = SyntheticBatchGenerator(seed=2026)
    batch_2026_b = gen_2026_b.generate_batch(count=50)
    serialized_b = SyntheticBatchGenerator.serialize_batch(batch_2026_b)

    # Verify seed 2026 is self-consistent
    assert serialized_a == serialized_b

    # Verify seed 2026 diverges from seed 42
    gen_42 = SyntheticBatchGenerator(seed=42)
    serialized_42 = SyntheticBatchGenerator.serialize_batch(gen_42.generate_batch(count=50))
    assert serialized_a != serialized_42


def test_synthetic_batch_distributions() -> None:
    """Verify realistic ranges and bounds for generated transactions."""
    gen = SyntheticBatchGenerator(seed=100)
    batch = gen.generate_batch(count=100)

    assert len(batch) == 100

    amounts = [b.attempt.amount_subunits for b in batch]
    # Min is >= 2000 paise (20 INR), max <= 5,000,000 paise (50,000 INR)
    assert min(amounts) >= 2000
    assert max(amounts) <= 5000000

    # Ensure integer subunit precision (no floats)
    assert all(isinstance(a, int) for a in amounts)

    # Unique order IDs and payment IDs
    order_ids = {b.opportunity.order_id for b in batch}
    payment_ids = {b.attempt.razorpay_payment_id for b in batch}
    assert len(order_ids) == 100
    assert len(payment_ids) == 100

    # Ensure all 5 failure categories are generated across a 100-sample batch
    categories = {b.opportunity.failure_category for b in batch}
    assert len(categories) >= 4  # At least 4 of the 5 present in 100 samples
