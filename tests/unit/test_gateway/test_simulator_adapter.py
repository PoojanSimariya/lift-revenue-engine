"""Unit tests for DeterministicSimulatorAdapter."""

import pytest
from lift.core.errors import GatewayResourceNotFoundError, GatewayTimeoutError
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.gateway.types import GatewayCustomerInfo


def test_simulator_create_and_fetch_payment_link():
    """Verify in-memory creation and retrieval of simulated Payment Links."""
    simulator = DeterministicSimulatorAdapter(seed_prefix="test")
    cust = GatewayCustomerInfo(
        name="Customer A",
        email="cust_a@example.com",
        contact="+919876543210",
    )

    result = simulator.create_payment_link(
        amount_subunits=250000,
        currency="INR",
        reference_id="ref_sim_001",
        description="Simulator Test Link",
        customer=cust,
        notes={"opportunity_id": "opp_123"},
    )

    assert result.id.startswith("plink_test_")
    assert result.status == "created"
    assert result.amount == 250000
    assert result.notes["opportunity_id"] == "opp_123"

    # Fetch by ID
    status = simulator.fetch_payment_link(result.id)
    assert status.id == result.id
    assert status.status == "created"
    assert status.amount == 250000

    # Discover by reference_id
    discovered = simulator.fetch_payment_link_by_reference_id("ref_sim_001")
    assert discovered is not None
    assert discovered.id == result.id

    # Non-existent reference_id returns None
    assert simulator.fetch_payment_link_by_reference_id("ref_non_existent") is None


def test_simulator_cancel_payment_link():
    """Verify cancellation of active Payment Links in simulator."""
    simulator = DeterministicSimulatorAdapter()
    result = simulator.create_payment_link(
        amount_subunits=10000,
        currency="INR",
        reference_id="ref_cancel_01",
        description="To Cancel",
        customer=GatewayCustomerInfo(),
    )
    assert simulator.cancel_payment_link(result.id) is True
    status = simulator.fetch_payment_link(result.id)
    assert status.status == "cancelled"


def test_simulator_timeout_trigger():
    """Verify simulate_network_timeout_on_next_call raises on next call and resets."""
    simulator = DeterministicSimulatorAdapter()
    simulator.simulate_network_timeout_on_next_call()

    with pytest.raises(GatewayTimeoutError):
        simulator.fetch_payment("pay_sim_001")

    # Subsequent call does not raise timeout (it resets)
    with pytest.raises(GatewayResourceNotFoundError):
        simulator.fetch_payment("pay_sim_001")


def test_simulator_simulate_payment_and_order():
    """Verify seeding and fetching orders, payments, and order-payments."""
    simulator = DeterministicSimulatorAdapter()
    order = simulator.simulate_order("order_001", amount=150000, status="created")
    assert order.id == "order_001"
    assert order.amount == 150000

    payment1 = simulator.simulate_payment(
        "pay_001",
        amount=150000,
        order_id="order_001",
        status="failed",
    )
    assert payment1.id == "pay_001"
    assert payment1.status == "failed"

    payment2 = simulator.simulate_payment(
        "pay_002",
        amount=150000,
        order_id="order_001",
        status="captured",
    )
    assert payment2.id == "pay_002"
    assert payment2.status == "captured"

    payments = simulator.fetch_order_payments("order_001")
    assert len(payments) == 2
    assert [p.id for p in payments] == ["pay_001", "pay_002"]


def test_simulator_simulate_payment_link_paid():
    """Verify simulate_payment_link_paid updates state to paid."""
    simulator = DeterministicSimulatorAdapter()
    simulator.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id="ref_paid_001",
        description="Link to Pay",
        customer=GatewayCustomerInfo(),
    )
    paid_link = simulator.simulate_payment_link_paid("ref_paid_001")
    assert paid_link.status == "paid"
    assert paid_link.amount_paid == 50000
