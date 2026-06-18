"""Maps application-layer errors to HTTP responses. This mapping is an interface
concern — the application raises semantic errors, the API decides the status."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.application.errors import (
    ApplicationError,
    AppraisalNotFound,
    AppraisalTooLarge,
    AppraisalTooManyEsiTypes,
    AuthorizingCharacterNotInCorporation,
    CharacterNotInCorporation,
    CorporationAlreadyRegistered,
    CorporationNotRegistered,
    DeliveryLocationInvalid,
    DeliveryLocationRequired,
    EmptyAppraisal,
    LocationInvalid,
    LocationNotFound,
    ManagerAlreadyExists,
    ManagerNotFound,
    MarketHubInvalid,
    NotAuthorized,
    NotAuthorizedToAuthorizeStructure,
    PricingRuleNotFound,
    PricingRuleTargetInvalid,
    RosterAccessDenied,
    RosterRefreshTooSoon,
    SsoNotConfigured,
    StructureEncryptionNotConfigured,
    StructureMarketUnavailable,
    StructureTokenExpired,
    StructureTokenMissing,
)

_STATUS: dict[type[ApplicationError], int] = {
    SsoNotConfigured: 503,
    NotAuthorized: 403,
    CorporationAlreadyRegistered: 409,
    CorporationNotRegistered: 404,
    CharacterNotInCorporation: 400,
    ManagerAlreadyExists: 409,
    ManagerNotFound: 404,
    RosterAccessDenied: 403,
    RosterRefreshTooSoon: 429,
    AuthorizingCharacterNotInCorporation: 403,
    PricingRuleNotFound: 404,
    PricingRuleTargetInvalid: 400,
    AppraisalNotFound: 404,
    EmptyAppraisal: 400,
    AppraisalTooLarge: 422,
    AppraisalTooManyEsiTypes: 422,
    MarketHubInvalid: 422,
    NotAuthorizedToAuthorizeStructure: 403,
    StructureEncryptionNotConfigured: 503,
    StructureTokenMissing: 409,
    StructureTokenExpired: 409,
    StructureMarketUnavailable: 409,
    LocationInvalid: 422,
    LocationNotFound: 404,
    DeliveryLocationRequired: 422,
    DeliveryLocationInvalid: 422,
}


async def application_error_handler(
    request: Request, exc: ApplicationError
) -> JSONResponse:
    status_code = _STATUS.get(type(exc), 400)
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})
