"""Use-case errors. The application layer raises these on business-rule
violations; the interface layer maps each type to an HTTP status (it owns the
HTTP concern, not the application)."""


class ApplicationError(Exception):
    default_detail = "Application error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.default_detail)


class SsoNotConfigured(ApplicationError):
    default_detail = "EVE SSO is not configured"


class NotAuthorized(ApplicationError):
    default_detail = "Not authorized"


class CorporationAlreadyRegistered(ApplicationError):
    default_detail = "Corporation is already registered"


class CorporationNotRegistered(ApplicationError):
    default_detail = "Corporation is not registered"


class CharacterNotInCorporation(ApplicationError):
    default_detail = "Character is not a member of your corporation"


class ManagerAlreadyExists(ApplicationError):
    default_detail = "Character is already a manager"


class ManagerNotFound(ApplicationError):
    default_detail = "Manager not found"


class PricingRuleNotFound(ApplicationError):
    default_detail = "Pricing rule not found"


class PricingRuleTargetInvalid(ApplicationError):
    default_detail = "Pricing rule target does not exist"


class AppraisalNotFound(ApplicationError):
    default_detail = "Appraisal not found"


class EmptyAppraisal(ApplicationError):
    default_detail = "An appraisal must contain at least one item"


class AppraisalTooLarge(ApplicationError):
    default_detail = "An appraisal may contain at most 1000 items (EVE's contract limit)"
