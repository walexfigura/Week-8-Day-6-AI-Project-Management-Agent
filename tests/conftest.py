"""Shared fixtures: every test gets its own throwaway state directory (never touches state/)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_manager_agent.actions import apply_approved
from project_manager_agent.agent import offline_investigate
from project_manager_agent.approvals.requests import decide
from project_manager_agent.cli import init_state
from project_manager_agent.config import load_settings
from project_manager_agent.scenarios import simulate_reply
from project_manager_agent.storage import open_store

ROOT = Path(__file__).resolve().parents[1]


class Env:
    def __init__(self, tmp_path: Path, scenario: str = "launch-delay"):
        self.settings = load_settings(ROOT, state_dir=tmp_path / "state")
        self.settings.openai_api_key = None
        init_state(self.settings, scenario)
        self.store = open_store(self.settings)
        self.policy = self.settings.load_policy()

    def approve_and_apply(self, approval_id: str, approver: str = "Test Human") -> dict:
        decide(self.store, approval_id, approve=True, approver=approver)
        return apply_approved(self.store, self.policy, approval_id)

    def draft_sarah(self) -> dict:
        r = offline_investigate(self.store, self.policy)
        assert r["followups_drafted"], r
        return self.store.get_followup(r["followups_drafted"][0])

    def send_sarah(self) -> dict:
        fu = self.draft_sarah()
        self.approve_and_apply(fu["approval_id"])
        return self.store.get_followup(fu["followup_id"])

    def reply(self, key: str, interpreter=None) -> list[dict]:
        return simulate_reply(self.store, self.policy, self.settings.data_dir, key, interpreter=interpreter)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def make_env(tmp_path: Path):
    counter = {"n": 0}

    def _make(scenario: str) -> Env:
        counter["n"] += 1
        return Env(tmp_path / f"s{counter['n']}", scenario)
    return _make
