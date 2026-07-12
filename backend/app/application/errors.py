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
        "EVE wouldn't return your corporation's member list for the connected character "
        "— it doesn't have permission to read the roster. Reconnect with a character "
        "that can (e.g. one with the in-game roles to view corp members)."
    )


class RosterRefreshTooSoon(ApplicationError):
    default_detail = (
        "The corp roster was refreshed recently — try again in a few minutes "
        "(it also refreshes automatically every day)"
    )


class AuthorizingCharacterNotInCorporation(ApplicationError):
    default_detail = (
        "The character you authorized with isn't a member of your corporation — "
        "connect with a character that belongs to it"
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
    default_detail = "Only the CEO or a Director can connect or revoke corp ESI access"


class StructureEncryptionNotConfigured(ApplicationError):
    default_detail = (
        "Structure-market encryption key (BUYBACK_TOKEN_ENCRYPTION_KEY) is missing "
        "or not a valid Fernet key — generate one with: python -c \"from "
        "cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


class CorpEsiTokenMissing(ApplicationError):
    default_detail = "Corp ESI access has not been connected"


class CorpEsiTokenExpired(ApplicationError):
    default_detail = "Corp ESI access has expired; please reconnect"


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


class NoMatchedContract(ApplicationError):
    """No matched in-game contract is linked to this appraisal (ADR-0038), so there's
    nothing to open in EVE."""

    default_detail = "No matching in-game contract is linked to this appraisal yet"


class GrantExpiryInPast(ApplicationError):
    default_detail = (
        "The access end date is in the past — pick a future date, or leave it "
        "empty for access that never expires"
    )


class OperatorWalletMissing(ApplicationError):
    default_detail = "The operator wallet has not been connected"


class OperatorWalletExpired(ApplicationError):
    default_detail = "The operator wallet authorization has expired; please reconnect"


class PaymentNotFound(ApplicationError):
    default_detail = "Payment not found"


class PaymentAlreadyMatched(ApplicationError):
    default_detail = "This payment has already been applied to a corporation"


class PaymentTooSmall(ApplicationError):
    default_detail = (
        "This payment is smaller than the price of one access period — if you still "
        "want to grant access for it, use Give access instead"
    )


class EntitlementRequired(ApplicationError):
    """The corp holds no active entitlement for a paid feature (ADR-0042). Mapped to
    402 Payment Required; the detail stays plain-English (no accounting jargon)."""

    default_detail = (
        "Your corporation doesn't have access to this feature — an app admin can "
        "grant it, or it unlocks when your corporation's access payment is received"
    )


class OpenContractUnavailable(ApplicationError):
    """The session can't open the contract in EVE (ADR-0038): it holds no login token, the
    token was revoked, or it lacks the open-window scope. The fix is always to log in again."""

    default_detail = "Log in again to enable opening contracts in EVE"


class HangarLocationUnknown(ApplicationError):
    """The buyback-hangar location isn't one of the corp's drop-off locations
    (ADR-0044) — the hangar picker offers only those."""

    default_detail = (
        "That location isn't one of your corporation's drop-off locations — add it "
        "on the Locations page first"
    )
