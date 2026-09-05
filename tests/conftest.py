"""Pytest shared fixtures for LIFT test suite."""

import secrets
from datetime import datetime, timezone

import pytest
from lift.core.types import AttemptStatus, FailureCategory, OpportunityState, PaymentMethod
from lift.domain.models import Customer, Merchant, PaymentAttempt, RecoveryOpportunity


@pytest.fixture
def merchant_salt() -> str:
    return secrets.token_hex(32)


@pytest.fixture
def sample_merchant(merchant_salt: str) -> Merchant:
    return Merchant(
        name="Test Merchant Pvt Ltd",
        default_currency="INR",
        timezone="Asia/Kolkata",
        idempotency_salt=merchant_salt,
    )


@pytest.fixture
def sample_customer(sample_merchant: Merchant) -> Customer:
    return Customer(
        merchant_id=sample_merchant.id,
        external_customer_id="cust_test_12345",
        phone_hash="hash_phone_9999999999",
        email_hash="hash_email_test@example.com",
        risk_tier=1,
        rolling_contacts_7d=0,
        last_contacted_at=None,
    )


@pytest.fixture
def sample_attempt(sample_customer: Customer) -> PaymentAttempt:
    return PaymentAttempt(
        customer_id=sample_customer.id,
        razorpay_payment_id="pay_test_001",
        razorpay_order_id="order_test_001",
        attempt_sequence=1,
        amount_subunits=450000,  # 4,500.00 INR
        currency="INR",
        payment_method=PaymentMethod.CARD,
        status=AttemptStatus.FAILED,
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment authentication timed out",
        error_source="bank",
        error_step="payment_authentication",
        error_reason="3ds_timeout",
        gateway_created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_opportunity(
    sample_merchant: Merchant, sample_customer: Customer, sample_attempt: PaymentAttempt
) -> RecoveryOpportunity:
    return RecoveryOpportunity(
        merchant_id=sample_merchant.id,
        customer_id=sample_customer.id,
        order_id="order_test_001",
        initial_attempt_id=sample_attempt.id,
        latest_attempt_id=sample_attempt.id,
        amount_at_risk_subunits=sample_attempt.amount_subunits,
        currency="INR",
        current_state=OpportunityState.OPEN,
        failure_category=FailureCategory.AUTHENTICATION_TIMEOUT,
        organic_recovery_estimate=0.30,
        failure_attempt_count=1,
        total_interventions_count=0,
        total_contacts_count=0,
    )
