from types import SimpleNamespace

import pytest

from ntrp.integrations.google_auth import auth


class Flow:
    def __init__(self, warning: Warning):
        self.warning = warning
        self.oauth2session = SimpleNamespace(token={})
        self.credentials = SimpleNamespace(scopes=())

    def run_local_server(self, **_kwargs):
        raise self.warning


def _scope_warning(old: set[str], new: set[str]) -> Warning:
    warning = Warning("scope changed")
    warning.old_scope = old
    warning.new_scope = new
    warning.token = {"access_token": "token", "scope": list(new)}
    return warning


def test_oauth_accepts_google_scope_expansion():
    flow = Flow(_scope_warning({"drive"}, {"drive", "gmail"}))

    credentials = auth._run_local_oauth(flow)

    assert flow.oauth2session.token["access_token"] == "token"
    assert credentials is flow.credentials


def test_oauth_rejects_scope_reduction():
    flow = Flow(_scope_warning({"drive", "gmail"}, {"drive"}))

    with pytest.raises(Warning, match="scope changed"):
        auth._run_local_oauth(flow)
