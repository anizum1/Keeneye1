"""Shared fixtures.  Every test gets a throwaway workspace on tmp_path."""

from __future__ import annotations

import pytest

from coc.auth import Examiner
from coc.service import Workspace


@pytest.fixture()
def workspace(tmp_path) -> Workspace:
    return Workspace.initialize(tmp_path / "ws")


@pytest.fixture()
def examiner(workspace: Workspace) -> Examiner:
    return workspace.create_user("kim", "s3cret-passphrase", full_name="K. Alvarez", role="admin")


@pytest.fixture()
def second_examiner(workspace: Workspace, examiner: Examiner) -> Examiner:
    return workspace.create_user("raj", "another-passphrase", full_name="R. Mehta", actor=examiner)


@pytest.fixture()
def case(workspace: Workspace, examiner: Examiner):
    return workspace.open_case("2026-014", "Laptop seizure", examiner)


@pytest.fixture()
def evidence_file(tmp_path):
    path = tmp_path / "disk.dd"
    path.write_bytes(b"\x00EVIDENCE\xff" * 2048)
    return path
