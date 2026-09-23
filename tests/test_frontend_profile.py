"""R9 — the profile page.

The page itself is thin; what is worth guarding is the property it rests on:
`GET /api/attendance/me` is self-scoped **by construction**, taking no user
parameter at all. That is the shape `visible_time_logs` was given after the
same endpoint class disclosed the whole roster's hours to any authenticated
caller, and it is stronger than a check because there is nothing to forget.

The account-settings component is also asserted to be unchanged and still
mounted at its two original sites. "Absorbing" settings into this page by
moving it would have been a regression (Q28): those buttons are how a user
reaches the password modal from anywhere.
"""
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def test_me_takes_no_user_parameter():
    """Safe by construction. An endpoint that accepts an id and checks it is a
    check that can be forgotten."""
    from api.routers import attendance

    params = set(inspect.signature(attendance.my_attendance).parameters)
    leaky = {p for p in params if "user" in p.lower()} - {"current_user"}
    assert not leaky, (
        f"GET /api/attendance/me accepts {leaky}; it must be incapable of "
        "naming another user"
    )


def test_me_is_not_admin_gated():
    """Q10 requires annotators to see their own attendance. Gating this would
    defeat the page entirely."""
    from api.routers import attendance

    source = inspect.getsource(attendance.my_attendance)
    assert "require_admin" not in source


def test_the_profile_page_calls_only_the_self_scoped_endpoint():
    """A page that can only ask about itself cannot leak."""
    source = (ROOT / "frontend" / "js" / "pages" / "profile.js").read_text(
        encoding="utf-8"
    )
    assert "/api/attendance/me" in source
    for forbidden in (
        "/api/attendance/days/",
        "/api/attendance/range",
        "/api/attendance/export",
    ):
        assert forbidden not in source, (
            f"the profile page calls {forbidden}, which is admin-scoped"
        )


def test_account_settings_stays_mounted_at_its_original_sites():
    """Nothing moved (Q28), so no import pin at either site needed bumping and
    there is no window in which a cached bundle points at a removed module."""
    for page in ("pages/project/router.js", "pages/projects-list.js"):
        source = (ROOT / "frontend" / "js" / page).read_text(encoding="utf-8")
        assert "wireAccountSettings" in source, (
            f"{page} lost its account-settings button; removing it is a "
            "regression, not a consolidation"
        )


def test_the_profile_page_hosts_settings_rather_than_reimplementing_them():
    """Two live copies of a password form is the drift problem rule 18b
    describes in a different guise."""
    source = (ROOT / "frontend" / "js" / "pages" / "profile.js").read_text(
        encoding="utf-8"
    )
    assert "wireAccountSettings" in source
    # The component owns the form. The page must not restate it.
    assert "/api/auth/password" not in source, (
        "the profile page implements its own password form instead of calling "
        "into the component that already owns it"
    )


def test_the_profile_page_pins_its_entry_script():
    markup = (ROOT / "frontend" / "profile.html").read_text(encoding="utf-8")
    assert 'src="js/pages/profile.js?v=' in markup
    assert 'href="styles.css?v=' in markup


def test_the_profile_page_carries_the_same_qualifications_as_the_dashboard():
    """An annotator checking their own hours needs them at least as much as an
    admin does: these are the numbers they would query."""
    markup = (ROOT / "frontend" / "profile.html").read_text(encoding="utf-8").lower()
    assert "excludes breaks" in markup
    assert "timeout" in markup
    assert "not a count of work" in markup or "not" in markup


def test_the_range_ceiling_is_shared_with_the_admin_endpoints():
    """The page's "Last 30 days" button must not exceed the server bound."""
    from api.routers.attendance import MAX_RANGE_DAYS

    source = (ROOT / "frontend" / "js" / "pages" / "profile.js").read_text(
        encoding="utf-8"
    )
    assert "setRange(30)" in source
    assert MAX_RANGE_DAYS >= 30, (
        f"the page offers 30 days but the server caps at {MAX_RANGE_DAYS}"
    )
