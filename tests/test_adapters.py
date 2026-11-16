"""Monday.com normalization against a fixture shaped like the documented GraphQL response.
(No live API call is made anywhere in this repository.)"""

import json

import pytest

from project_manager_agent.integrations.adapters import (
    MappingError,
    MondayColumnMapping,
    normalize_monday_item,
)

ITEM = {
    "id": "1111111111", "name": "Complete product photography", "group": {"id": "creative", "title": "Creative"},
    "column_values": [
        {"id": "status", "type": "status", "text": "Working on it", "value": json.dumps({"index": 0})},
        {"id": "person", "type": "people", "text": "Sarah Patel", "value": json.dumps({"personsAndTeams": [{"id": 48202303, "kind": "person"}]})},
        {"id": "date4", "type": "date", "text": "2026-03-09", "value": json.dumps({"date": "2026-03-09"})},
        {"id": "dependency", "type": "dependency", "text": "Finalize specs", "linked_item_ids": ["2222222222"], "value": None},
        {"id": "priority", "type": "status", "text": "Critical", "value": None},
    ],
}


def mapping():
    return MondayColumnMapping(monday_user_to_member={"48202303": "TM-SARAH"})


def test_normalize_item():
    t = normalize_monday_item(ITEM, mapping(), "4471")
    assert t["status"] == "In Progress" and t["owner_id"] == "TM-SARAH" and t["due_date"] == "2026-03-09"
    assert t["depends_on"] == ["MON-2222222222"] and t["priority"] == "Critical"


def test_unmapped_status_is_an_error_not_a_guess():
    item = json.loads(json.dumps(ITEM))
    item["column_values"][0]["text"] = "Waiting on legal"
    with pytest.raises(MappingError):
        normalize_monday_item(item, mapping(), "4471")


def test_unmapped_person_and_missing_column():
    with pytest.raises(MappingError):
        normalize_monday_item(ITEM, MondayColumnMapping(), "4471")
    with pytest.raises(MappingError):
        normalize_monday_item(ITEM, MondayColumnMapping(due_date_column="timeline", monday_user_to_member={"48202303": "TM-SARAH"}), "4471")
