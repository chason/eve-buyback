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


class RosterAccessDenied(ApplicationError):
    default_detail = (
        "EVE refused the corporation member list — the character you synced with must "
        "be a Director (and grant the membership scope) to read the roster"
    )


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


class AppraisalTooManyEsiTypes(ApplicationError):
    default_detail = (
        "This appraisal prices too many distinct items at a non-Fuzzwork market "
        "(each one is a separate live market lookup). Split it into smaller appraisals, "
        "or price at a major trade hub."
    )


class MarketHubInvalid(ApplicationError):
    default_detail = "Market hub could not be resolved"


class NotAuthorizedToAuthorizeStructure(ApplicationError):
    default_detail = "Only a Buyback Manager can authorize structure access"


class StructureEncryptionNotConfigured(ApplicationError):
    default_detail = (
        "Structure-market encryption key (BUYBACK_TOKEN_ENCRYPTION_KEY) is missing "
        "or not a valid Fernet key — generate one with: python -c \"from "
        "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


class StructureTokenMissing(ApplicationError):
    default_detail = "Structure access has not been authorized"


class StructureTokenExpired(ApplicationError):
    default_detail = "Structure access has expired; please re-authorize"


class StructureMarketUnavailable(ApplicationError):
    default_detail = (
        "Structure-market pricing is currently unavailable — a Buyback Manager must "
        "re-authorize structure access before items can be priced at the corp's "
        "structure hub."
    )


class LocationInvalid(ApplicationError):
    default_detail = "Drop-off location could not be resolved"


class LocationNotFound(ApplicationError):
    default_detail = "Drop-off location not found"


class DeliveryLocationRequired(ApplicationError):
    default_detail = "Select a drop-off location for this appraisal"


class DeliveryLocationInvalid(ApplicationError):
    default_detail = "That drop-off location is not accepted by your corporation"
