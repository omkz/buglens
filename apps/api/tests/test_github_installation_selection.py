import pytest

from app.integrations.github.client import GitHubInstallation
from app.integrations.github.selection import (
    InstallationSelectionError,
    select_installation,
)


def make_installation(
    installation_id: int, app_id: int, account_login: str = "octocat"
) -> GitHubInstallation:
    return GitHubInstallation(
        id=installation_id, app_id=app_id, account_login=account_login
    )


def test_valid_explicit_installation_id_is_selected():
    installations = [
        make_installation(1, app_id=999),
        make_installation(2, app_id=1),
    ]

    result = select_installation(
        installations, app_id="999", requested_installation_id=1
    )

    assert result.id == 1


def test_forged_or_unavailable_installation_id_is_rejected():
    installations = [make_installation(1, app_id=999)]

    with pytest.raises(InstallationSelectionError) as exc_info:
        select_installation(
            installations, app_id="999", requested_installation_id=404
        )

    assert exc_info.value.code == "app_not_installed"


def test_explicit_installation_id_with_wrong_app_id_is_rejected():
    # The installation exists and is genuinely the user's, but it belongs
    # to a different app than the one configured for Buglensa.
    installations = [make_installation(1, app_id=1)]

    with pytest.raises(InstallationSelectionError) as exc_info:
        select_installation(
            installations, app_id="999", requested_installation_id=1
        )

    assert exc_info.value.code == "app_not_installed"


def test_no_matching_installation_returns_app_not_installed():
    installations = [make_installation(1, app_id=1)]

    with pytest.raises(InstallationSelectionError) as exc_info:
        select_installation(
            installations, app_id="999", requested_installation_id=None
        )

    assert exc_info.value.code == "app_not_installed"


def test_exactly_one_matching_installation_is_selected():
    installations = [
        make_installation(1, app_id=1),
        make_installation(2, app_id=999),
    ]

    result = select_installation(
        installations, app_id="999", requested_installation_id=None
    )

    assert result.id == 2


def test_multiple_matching_installations_without_explicit_id_is_ambiguous():
    installations = [
        make_installation(1, app_id=999, account_login="org-a"),
        make_installation(2, app_id=999, account_login="org-b"),
    ]

    with pytest.raises(InstallationSelectionError) as exc_info:
        select_installation(
            installations, app_id="999", requested_installation_id=None
        )

    assert exc_info.value.code == "ambiguous_installation"
