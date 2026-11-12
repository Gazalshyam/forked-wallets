"""All tables. Money columns are integer cents (`*_minor`). Never floats."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Labels required by the hard rules. Every ledger row and UI badge uses one of these.
LABEL_PAYPAL = "PAYPAL SANDBOX"
LABEL_MOCK_PAYPAL = "MOCK PAYPAL"
LABEL_SIMULATED = "SIMULATED"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(300))
    paypal_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="MEMBER")  # OWNER | MEMBER
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE | LEFT
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AUTO_DEBIT | APPROVAL_EACH_CYCLE
    payment_mode: Mapped[str] = mapped_column(String(30), default="APPROVAL_EACH_CYCLE")
    custom_share_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vault_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted, never logged
    vault_label: Mapped[str | None] = mapped_column(String(30), nullable=True)  # PAYPAL SANDBOX | MOCK PAYPAL
    force_fail_next: Mapped[bool] = mapped_column(Boolean, default=False)  # demo control
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    invited_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | ACCEPTED | DECLINED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), unique=True)
    merchant_name: Mapped[str] = mapped_column(String(120), default="StreamFlix")
    price_minor: Mapped[int] = mapped_column(BigInteger)
    cadence_days: Mapped[int] = mapped_column(Integer, default=30)
    next_renewal_date: Mapped[date] = mapped_column(Date)
    split_mode: Mapped[str] = mapped_column(String(20), default="EQUAL")  # EQUAL | CUSTOM
    # ACTIVE | COLLECTING | FUNDED | PAUSED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # Group decision after a member leaves (E1): {id, kind, status OPEN|AGREED|PAUSED, target current|next,
    # cycle_id, left, voters, new_shares, votes}. Null when there has never been one.
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (UniqueConstraint("subscription_id", "renewal_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    renewal_date: Mapped[date] = mapped_column(Date)
    required_minor: Mapped[int] = mapped_column(BigInteger)
    # membership_id (as string) -> share in cents. Immutable snapshot for this cycle.
    shares: Mapped[dict] = mapped_column(JSON, default=dict)
    # membership_id (as string) -> human note explaining a share adjustment (E2 cover repayment).
    share_notes: Mapped[dict] = mapped_column(JSON, default=dict)
    # E3: membership_id (as string) -> cents of the share that repays a cover (+ for the debtor, - for the coverer).
    # What the renewal costs each member is shares - share_adjustments; the adjustment moves money between them.
    share_adjustments: Mapped[dict] = mapped_column(JSON, default=dict)
    # OPEN | COLLECTING | FUNDED | RENEWED | PAUSED
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContributionAttempt(Base):
    __tablename__ = "contribution_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("memberships.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(20), default="SHARE")  # SHARE | TOP_UP | COVER | REFUND
    # COVER only: the member whose share this payment settles (membership_id is the payer).
    covered_membership_id: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"), nullable=True)
    method: Mapped[str] = mapped_column(String(20))  # APPROVAL | AUTO_DEBIT | REFUND
    # REFUND only (E3): PayPal's id for the refund of `paypal_capture_id` back to this member.
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    want_vault: Mapped[bool] = mapped_column(Boolean, default=False)
    # CREATED | APPROVAL_PENDING | COMPLETED | FAILED | CANCELLED | PENDING (refund not final yet)
    status: Mapped[str] = mapped_column(String(20), default="CREATED")
    source_label: Mapped[str] = mapped_column(String(30))
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LedgerEntry(Base):
    """Append-only. Entry types:
    CONTRIBUTION (+ to collected), SPEND (+ to spent), FORK (opens a reservation),
    MERGE_BACK (returns unused reservation), REFUND (E3: money PayPal returned to a member who left),
    WRITE_OFF (E3: moves a departed member's unpaid debt onto the members holding the pot; no money moves).
    reserved = FORK - task SPEND - MERGE_BACK.

    `allocation` says whose money a row is (membership id as string -> cents). For SPEND, FORK and MERGE_BACK the
    values are non-negative and sum to amount_minor. For WRITE_OFF they are signed balance reductions summing to 0.
    CONTRIBUTION and REFUND rows belong to `membership_id` and carry no allocation.
    """

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(20))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    source_label: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text, default="")
    membership_id: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"), nullable=True)  # who paid
    # COVER contributions: whose cycle share this row settles. Null means the payer's own share.
    on_behalf_of_membership_id: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"), nullable=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("contribution_attempts.id"), nullable=True)
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_payout_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    allocation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    prompt: Mapped[str] = mapped_column(Text)
    budget_minor: Mapped[int] = mapped_column(BigInteger)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    # AWAITING_CONSENT | SEARCHING | LOCKED | BOOKING | COMPLETED | NEEDS_REVIEW | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="SEARCHING")
    # E4: whose money funds this task (membership ids). Only their balances form the sub-pot.
    participants: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # E4: membership id (string) -> AGREE | DECLINE | OWNER_AUTHORIZED (silent past the deadline)
    consent: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    consent_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # E4: the fork split per participant (membership id string -> cents), fixed when the agents start.
    allocation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    booking_mode: Mapped[str] = mapped_column(String(20), default="CONFIRMED")  # demo toggle
    winner_agent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    winner_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lock_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booking_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booking_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    final_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    merged_back_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payout_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payout_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payout_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(40))
    catalog: Mapped[str] = mapped_column(String(40))
    # QUEUED | SEARCHING | FOUND | WON | LOST | NO_MATCH | CANCELLED | ERROR
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    hotel_ref: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80))
    nightly_minor: Mapped[int] = mapped_column(BigInteger)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    rating: Mapped[str] = mapped_column(String(8))  # stored as text, e.g. "4.3" (not money)
    # PROPOSED | WON | LOST | DECLINED
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MockCard(Base):
    __tablename__ = "mock_cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    last4: Mapped[str] = mapped_column(String(4))
    limit_minor: Mapped[int] = mapped_column(BigInteger)
    merchant_lock: Mapped[str] = mapped_column(String(40), default="DEMO_HOTEL")
    single_use: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE | USED | CLOSED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(30))  # COLLECT | CHECK | RENEW | RUN_TASK
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)  # PENDING|RUNNING|DONE|FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # PAY_SHARE | REMINDER | INFO | INVITE | COVER_REQUEST | DECISION | CONSENT
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    email_status: Mapped[str] = mapped_column(String(60), default="NOT_SENT (demo: email disabled)")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
