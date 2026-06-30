import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "authentication"))

from permissions import (
    has_permission,
    has_any_permission,
    can_manage_target_role,
    role_level,
    is_known_role,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    ROLE_ADMINISTRATOR,
    PERM_DASHBOARD_VIEW,
    PERM_LIVE_SCAN_VIEW,
    PERM_TRANSACTIONS_VIEW,
    PERM_REPORTS_VIEW,
    PERM_DIAGNOSTICS_VIEW,
    PERM_EXPORT_RUN,
    PERM_SETTINGS_WRITE,
    PERM_CALIBRATION_RUN,
    PERM_USERS_MANAGE,
    ALL_PERMISSIONS,
)


def test_operator_has_exactly_spec_permissions():
    granted = {p for p in ALL_PERMISSIONS if has_permission(ROLE_OPERATOR, p)}
    assert granted == {PERM_DASHBOARD_VIEW, PERM_LIVE_SCAN_VIEW, PERM_TRANSACTIONS_VIEW}


def test_operator_cannot_change_settings():
    assert has_permission(ROLE_OPERATOR, PERM_SETTINGS_WRITE) is False


def test_operator_cannot_manage_users():
    assert has_permission(ROLE_OPERATOR, PERM_USERS_MANAGE) is False


def test_operator_cannot_run_calibration():
    assert has_permission(ROLE_OPERATOR, PERM_CALIBRATION_RUN) is False


def test_supervisor_has_operator_permissions_plus_extras():
    operator_perms = {p for p in ALL_PERMISSIONS if has_permission(ROLE_OPERATOR, p)}
    supervisor_perms = {p for p in ALL_PERMISSIONS if has_permission(ROLE_SUPERVISOR, p)}
    assert operator_perms.issubset(supervisor_perms)
    assert PERM_REPORTS_VIEW in supervisor_perms
    assert PERM_DIAGNOSTICS_VIEW in supervisor_perms
    assert PERM_EXPORT_RUN in supervisor_perms


def test_supervisor_still_cannot_manage_users_or_settings():
    assert has_permission(ROLE_SUPERVISOR, PERM_USERS_MANAGE) is False
    assert has_permission(ROLE_SUPERVISOR, PERM_SETTINGS_WRITE) is False


def test_administrator_has_full_access():
    for perm in ALL_PERMISSIONS:
        assert has_permission(ROLE_ADMINISTRATOR, perm) is True


def test_administrator_passes_even_unknown_future_permission():
    # Deliberate fail-open-for-admin-only safety net (see permissions.py docstring).
    assert has_permission(ROLE_ADMINISTRATOR, "some.future.permission") is True


def test_unknown_role_fails_closed_for_non_admin():
    assert has_permission("not_a_real_role", PERM_DASHBOARD_VIEW) is False


def test_has_any_permission():
    assert has_any_permission(ROLE_OPERATOR, [PERM_SETTINGS_WRITE, PERM_DASHBOARD_VIEW]) is True
    assert has_any_permission(ROLE_OPERATOR, [PERM_SETTINGS_WRITE, PERM_USERS_MANAGE]) is False


def test_role_level_ordering():
    assert role_level(ROLE_OPERATOR) < role_level(ROLE_SUPERVISOR) < role_level(ROLE_ADMINISTRATOR)


def test_can_manage_target_role_administrator_can_do_anything():
    assert can_manage_target_role(ROLE_ADMINISTRATOR, ROLE_ADMINISTRATOR) is True
    assert can_manage_target_role(ROLE_ADMINISTRATOR, ROLE_OPERATOR) is True


def test_can_manage_target_role_supervisor_cannot_manage_admin_or_peers():
    assert can_manage_target_role(ROLE_SUPERVISOR, ROLE_ADMINISTRATOR) is False
    assert can_manage_target_role(ROLE_SUPERVISOR, ROLE_SUPERVISOR) is False
    assert can_manage_target_role(ROLE_SUPERVISOR, ROLE_OPERATOR) is True


def test_is_known_role():
    assert is_known_role(ROLE_OPERATOR) is True
    assert is_known_role(ROLE_SUPERVISOR) is True
    assert is_known_role(ROLE_ADMINISTRATOR) is True
    assert is_known_role("not_a_role") is False
