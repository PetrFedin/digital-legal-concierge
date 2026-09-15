class CalculationRuleError(RuntimeError):
    """Base error for controlled legal calculation-rule failures."""


class CalculationRuleUnavailableError(CalculationRuleError):
    """No approved rule revision can lawfully be used for a new calculation."""


class CalculationRuleAmbiguousError(CalculationRuleError):
    """More than one approved rule revision matches the same calculation."""


class CalculationRuleValidationError(CalculationRuleError):
    """A draft rule cannot be approved because its controlled data is invalid."""


class ImmutableCalculationRuleError(CalculationRuleError):
    """An approved/retired revision was targeted by a draft-only mutation."""
