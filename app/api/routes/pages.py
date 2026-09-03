"""The two pages the administrator actually uses.

Mounted at the root rather than under ``/api`` because these are not API calls:
``/`` is the list of companies and ``/c/{company_id}`` is one company's thread.
The clause-path reports stay where they were, under ``/api/dashboard``, because
they are a different product surface with a different audience.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.intelligence.questions import fixed_question_set
from app.services.batch_service import company_listing, company_thread

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="app/dashboard/templates")

# Registered as a global rather than passed per route. The questionnaire is a
# versioned constant, so there is one correct value and no request can change it,
# and a template variable that every route has to remember to pass is a variable a
# route will eventually forget: Jinja's default Undefined renders a missing name as
# nothing at all, so forgetting it produced an empty questionnaire panel and a
# "0 questions" badge instead of an error anybody would notice.
templates.env.globals["questions"] = fixed_question_set()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    """Every company, most recently active first. No chat interface here.

    One thread per company, so this page is a list of threads and nothing else:
    clicking a row opens that company's conversation.
    """

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"companies": company_listing(db)},
    )


@router.get("/c/{company_id}")
def thread(request: Request, company_id: str, db: Session = Depends(get_db)):
    """One company's conversation, rendered once and kept current by the page."""

    try:
        payload = company_thread(db, company_id)
    except ValueError as exc:
        # A browser can retain a link from another local checkout/database.
        # Returning to the list is more useful than rendering a dead thread URL.
        if str(exc).startswith("No such company:"):
            return RedirectResponse(url="/", status_code=303)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # `questions` comes from the template globals above: the panel does not depend
    # on a round existing yet, which is what used to keep it empty until a CSV
    # upload created one.
    return templates.TemplateResponse(
        request=request,
        name="round.html",
        context={"thread": payload},
    )
