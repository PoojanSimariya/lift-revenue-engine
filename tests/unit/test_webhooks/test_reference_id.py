"""Unit tests for deterministic reference_id generation and validation."""

import uuid

from lift.webhooks.reference import generate_reference_id, is_valid_reference_id


def test_reference_id_determinism():
    """Verify that identical (opportunity_id, attempt_index) yields the exact same reference_id."""
    opp_id = uuid.uuid4()
    ref1 = generate_reference_id(opp_id, 0)
    ref2 = generate_reference_id(opp_id, 0)
    assert ref1 == ref2
    assert len(ref1) == 36
    assert ref1.startswith("ref_")


def test_reference_id_collision_resistance():
    """Verify collision resistance across distinct opportunity IDs and attempt indices."""
    refs = set()
    num_samples = 10000
    for i in range(num_samples):
        opp_id = uuid.uuid4()
        ref = generate_reference_id(opp_id, i % 5)
        refs.add(ref)

    assert len(refs) == num_samples, "Collision detected in generated reference IDs"


def test_reference_id_length_limit():
    """Verify that reference_id strictly satisfies Razorpay VARCHAR(40) limit."""
    opp_id = uuid.uuid4()
    for attempt in (0, 1, 10, 999, 1000000):
        ref = generate_reference_id(opp_id, attempt)
        assert len(ref) <= 40
        assert len(ref) == 36


def test_reference_id_is_not_used_as_decodable_opportunity_id():
    """Verify that reference_id is a one-way cryptographic hash and CANNOT be decoded to opp_id."""
    opp_id = uuid.uuid4()
    ref = generate_reference_id(opp_id, 1)

    # Assert that the string representation of opportunity_id is NOT in the reference_id
    assert str(opp_id) not in ref
    assert str(opp_id).replace("-", "") not in ref
    # Assert that attempt_index is not plaintext in reference_id
    assert not ref.endswith(":1")


def test_is_valid_reference_id():
    """Verify format validation for reference IDs."""
    opp_id = uuid.uuid4()
    valid_ref = generate_reference_id(opp_id, 0)
    assert is_valid_reference_id(valid_ref) is True

    # Invalid formats
    assert is_valid_reference_id("invalid_ref") is False
    assert is_valid_reference_id("ref_" + "a" * 31) is False  # 35 chars
    assert is_valid_reference_id("ref_" + "z" * 32) is False  # non-hex chars
    assert is_valid_reference_id(123) is False  # type: ignore
    assert is_valid_reference_id(None) is False  # type: ignore
