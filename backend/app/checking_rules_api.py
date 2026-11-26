import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from app.auth import require_user
from app.checking_rules import CheckingRuleDefaults, RulesConflict, RulesPage, RulesStorageError, RulesStore, RuleUpdate
from app.recorder import RecorderSettings

logger = logging.getLogger(__name__)


def rules_errors(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        yield
    except RulesConflict as error:
        raise HTTPException(409, str(error)) from None
    except (RulesStorageError, ValidationError) as error:
        logger.error("Checking rules operation failed (%s).", type(error).__name__)
        raise HTTPException(503, "Checking rules storage unavailable.") from None


router = APIRouter(prefix="/api/checking-rules", tags=["checking rules"],
                   dependencies=[Depends(require_user), Depends(rules_errors)])


def _store() -> RulesStore:
    return RulesStore(RecorderSettings().snapshot_dir, CheckingRuleDefaults().drift_seconds)


@router.get("", response_model=RulesPage)
def get_rules() -> RulesPage:
    store = _store()
    return RulesPage(rules=store.get(), defaults=store.defaults)


@router.put("", response_model=RulesPage)
def update_rules(update: RuleUpdate) -> RulesPage:
    store = _store()
    return RulesPage(rules=store.save(update), defaults=store.defaults)
