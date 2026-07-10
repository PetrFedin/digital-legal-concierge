NOTIFICATION_RULES = {
    "CASE_CREATED": {"recipients": ["admin"], "template": "case_created"},
    "LAWYER_ASSIGNED": {"recipients": ["lawyer"], "template": "lawyer_assigned"},
    "DOCUMENT_UPLOADED": {"recipients": ["admin", "lawyer"], "template": "document_uploaded"},
    "DOCUMENT_STATUS_CHANGED": {"recipients": ["client"], "template": "document_status_changed"},
    "PAYMENT_CREATED": {"recipients": ["client"], "template": "payment_created"},
    "PAYMENT_PAID": {"recipients": ["client", "admin"], "template": "payment_paid"},
    "PAYMENT_REMINDER": {"recipients": ["client"], "template": "payment_reminder"},
    "M2_CONSULTATION_BOOKED": {"recipients": ["client", "lawyer", "admin"], "template": "consultation_booked"},
    "CLAIM_30_DAYS_EXPIRED": {"recipients": ["lawyer", "admin"], "template": "claim_30_days_expired"},
    "COURT_STAGE_STARTED": {"recipients": ["client", "lawyer"], "template": "court_stage_started"},
}
