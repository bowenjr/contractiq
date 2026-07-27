"""Enumerations for ContractIQ's deterministic bid-management spine."""

from enum import Enum


class CustomerType(str, Enum):
    EPC = "epc"
    EPCM = "epcm"
    END_USER = "end_user"


class BidLevel(str, Enum):
    LEVEL_0 = "level_0"
    LEVEL_1 = "level_1"
    LEVEL_2 = "level_2"
    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"


class Gate(str, Enum):
    G0 = "g0"
    G1 = "g1"
    G2 = "g2"
    G3 = "g3"
    G4 = "g4"
    G5 = "g5"
    G6 = "g6"
    G7 = "g7"


class GateStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_REVIEW = "in_review"
    PASSED = "passed"
    HELD = "held"
    OVERRIDDEN = "overridden"


class BidStatus(str, Enum):
    ACTIVE = "active"
    HELD = "held"
    SUBMITTED = "submitted"
    WON = "won"
    LOST = "lost"
    NO_BID = "no_bid"


class RiskTrigger(str, Enum):
    NON_STANDARD_TERMS = "non_standard_terms"
    LIQUIDATED_DAMAGES = "liquidated_damages"
    BONDS_OR_GUARANTEES = "bonds_or_guarantees"
    EXTENDED_PAYMENT_OR_HOLDBACK = "extended_payment_or_holdback"
    NON_CANCELLABLE_PRODUCT = "non_cancellable_product"
    MULTIPLE_MANUFACTURERS = "multiple_manufacturers"
    SUBSTANTIAL_VENDOR_DATA = "substantial_vendor_data"
    INTERNATIONAL_EXPOSURE = "international_exposure"
    LONG_DURATION = "long_duration"
    FIELD_SERVICES = "field_services"
    EPC_FLOWDOWN = "epc_flowdown"
    WARRANTY_EXTENSION = "warranty_extension"
    UNCLEAR_SCOPE = "unclear_scope"


class ApprovalType(str, Enum):
    BID_NO_BID = "bid_no_bid"
    MARGIN = "margin"
    LEGAL = "legal"
    CREDIT = "credit"
    FINANCE = "finance"
    EXECUTIVE = "executive"


class ItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    APPROVED = "approved"
    ESCALATED = "escalated"


class Actor(str, Enum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class InferencePolicy(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_OK = "cloud_ok"


class PillarId(str, Enum):
    MONEY = "money"
    TIME = "time"
    SCOPE = "scope"
    RISK_LIABILITY = "risk_liability"
    RELATIONSHIPS = "relationships"
    ADMINISTRATION = "administration"
    EXIT = "exit"


class ObligationType(str, Enum):
    PERFORMANCE = "PERF"
    PAYMENT = "PAY"
    NOTICE = "NOTC"
    APPROVAL = "APPR"
    REPORTING = "RPT"
    INSURANCE = "INS"
    COMPLIANCE = "COMP"
    RESTRICTIVE = "REST"
    CONDITIONAL = "COND"
    SURVIVAL = "SURV"


class TriggerType(str, Enum):
    CALENDAR = "calendar"
    EVENT = "event"
    CONDITION = "condition"
    MILESTONE = "milestone"
    ROLLING = "rolling"
    CONTINUOUS = "continuous"
    NEGATIVE = "negative"


class NegotiationPriority(str, Enum):
    MUST_CHANGE = "must_change"
    SHOULD_CHANGE = "should_change"
    NICE_TO_CHANGE = "nice_to_change"
