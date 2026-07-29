NOTIFICATION_RULES = {
    "CASE_CREATED": {"recipients": ["admin"], "template": "case_created"},
    "LAWYER_ASSIGNED": {
        "recipients": ["lawyer"],
        "template": "lawyer_assigned",
    },
    "DOCUMENT_UPLOADED": {
        "recipients": ["admin", "lawyer"],
        "template": "document_uploaded",
    },
    "DOCUMENT_STATUS_CHANGED": {
        "recipients": ["client"],
        "template": "document_status_changed",
    },
    "PAYMENT_CREATED": {
        "recipients": ["client"],
        "template": "payment_created",
    },
    "PAYMENT_PAID": {
        "recipients": ["client", "admin"],
        "template": "payment_paid",
    },
    "PAYMENT_REMINDER": {
        "recipients": ["client"],
        "template": "payment_reminder",
    },
    "M2_CONSULTATION_BOOKED": {
        "recipients": ["client", "lawyer", "admin"],
        "template": "consultation_booked",
    },
    "CONSULTATION_RESCHEDULED": {
        "recipients": ["client", "lawyer", "admin"],
        "template": "consultation_rescheduled",
    },
    "CONSULTATION_REMINDER_24H": {
        "recipients": ["client", "lawyer"],
        "template": "consultation_reminder_24h",
    },
    "CONSULTATION_REMINDER_2H": {
        "recipients": ["client", "lawyer"],
        "template": "consultation_reminder_2h",
    },
    "CONSULTATION_COMPLETION_OVERDUE": {
        "recipients": ["lawyer", "admin"],
        "template": "consultation_completion_overdue",
    },
    "CONSULTATION_CANCELLATION_REQUESTED": {
        "recipients": ["client", "lawyer", "admin"],
        "template": "consultation_cancellation_requested",
    },
    "CONSULTATION_REFUNDED": {
        "recipients": ["client", "admin"],
        "template": "consultation_refunded",
    },
    "CONSULTATION_REFUND_DECLINED": {
        "recipients": ["client", "admin"],
        "template": "consultation_refund_declined",
    },
    "CONSULTATION_PAYMENT_REVIEW": {
        "recipients": ["admin"],
        "template": "consultation_payment_review",
    },
    "CLAIM_30_DAYS_EXPIRED": {
        "recipients": ["lawyer", "admin"],
        "template": "claim_30_days_expired",
    },
    "COURT_STAGE_STARTED": {
        "recipients": ["client", "lawyer"],
        "template": "court_stage_started",
    },
}
