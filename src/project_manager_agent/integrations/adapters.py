"""Adapter contracts and Monday.com schema normalization.

The agent's tools talk to *interfaces*, not vendors. The mock classes implement
these interfaces today. A live implementation would implement the same methods.

LIVE STATUS (be honest with readers):
* `normalize_monday_item` + `MondayColumnMapping` - implemented and unit-tested
  against a fixture shaped like the documented Monday.com GraphQL response.
* `MONDAY_ITEMS_QUERY` / `MONDAY_NEXT_PAGE_QUERY` - query text written from the
  official docs (items_page / next_items_page). NOT executed against a live account
  in this repository.
* No live Slack, Gmail or Monday.com *write* adapter is included. Live sending is
  prohibited by default policy. See docs/integrating-real-systems.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProjectBoard(Protocol):
    mode: str
    def get_project(self, project_id: str) -> dict[str, Any] | None: ...
    def get_tasks(self, project_id: str) -> list[dict[str, Any]]: ...
    def get_task(self, task_id: str) -> dict[str, Any] | None: ...
    def get_updates(self, task_id: str) -> list[dict[str, Any]]: ...
    def apply_approved_task_update(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class ChatSystem(Protocol):
    mode: str
    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def get_thread(self, thread_id: str) -> list[dict[str, Any]]: ...
    def send_approved_message(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class EmailSystem(Protocol):
    mode: str
    def get_thread(self, thread_id: str) -> dict[str, Any] | None: ...
    def search(self, query: str) -> list[dict[str, Any]]: ...
    def send_approved_email(self, approval_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------- Monday.com normalization
@dataclass
class MondayColumnMapping:
    """Map *your* board's column IDs and labels onto the canonical task schema.

    Every Monday.com board is configured differently: column IDs are generated
    (e.g. "status", "status_1", "project_status"), status labels are free text,
    and people columns hold Monday user IDs, not your directory IDs.
    """
    status_column: str = "status"
    owner_column: str = "person"
    due_date_column: str = "date4"
    dependency_column: str = "dependency"
    priority_column: str | None = "priority"
    status_labels: dict[str, str] = field(default_factory=lambda: {
        "Working on it": "In Progress", "Stuck": "Blocked", "Done": "Done", "Waiting": "Waiting",
        "At Risk": "At Risk", "": "Not Started", "Not Started": "Not Started"})
    monday_user_to_member: dict[str, str] = field(default_factory=dict)  # "48202303" -> "TM-SARAH"
    item_id_prefix: str = "MON-"

    @classmethod
    def from_json(cls, path: str) -> MondayColumnMapping:
        with open(path, encoding="utf-8") as fh:
            return cls(**json.load(fh))


MONDAY_ITEMS_QUERY = """
query ($boardId: [ID!], $limit: Int!) {
  boards(ids: $boardId) {
    id name
    items_page(limit: $limit) {
      cursor
      items {
        id name group { id title }
        column_values { id type text value
          ... on DependencyValue { linked_item_ids }
          ... on PeopleValue { persons_and_teams { id kind } }
        }
        updates(limit: 10) { id body created_at creator { id name } }
      }
    }
  }
}"""

MONDAY_NEXT_PAGE_QUERY = """
query ($cursor: String!, $limit: Int!) {
  next_items_page(cursor: $cursor, limit: $limit) {
    cursor
    items { id name column_values { id type text value } }
  }
}"""


class MappingError(ValueError):
    pass


def normalize_monday_item(item: dict[str, Any], mapping: MondayColumnMapping, board_id: str) -> dict[str, Any]:
    """Convert one Monday.com GraphQL item into the canonical task dict used by the agent.

    Unknown status labels and unmapped people raise MappingError instead of guessing.
    """
    cols = {c["id"]: c for c in item.get("column_values", [])}

    def col(cid: str | None) -> dict[str, Any]:
        if cid is None:
            return {}
        if cid not in cols:
            raise MappingError(f"Board {board_id} item {item['id']} has no column {cid!r}; check your column mapping.")
        return cols[cid]

    status_text = (col(mapping.status_column).get("text") or "").strip()
    if status_text not in mapping.status_labels:
        raise MappingError(f"Status label {status_text!r} is not mapped. Add it to status_labels.")
    owner_col = col(mapping.owner_column)
    people = owner_col.get("persons_and_teams")
    if people is None and owner_col.get("value"):
        people = json.loads(owner_col["value"]).get("personsAndTeams", [])
    persons = [p for p in (people or []) if p.get("kind") == "person"]
    owner_id = None
    if persons:
        monday_uid = str(persons[0]["id"])
        if monday_uid not in mapping.monday_user_to_member:
            raise MappingError(f"Monday user {monday_uid} is not mapped to a team member.")
        owner_id = mapping.monday_user_to_member[monday_uid]
    due_raw = col(mapping.due_date_column)
    due = None
    if due_raw.get("value"):
        due = json.loads(due_raw["value"]).get("date")
    dep_col = col(mapping.dependency_column)
    linked = dep_col.get("linked_item_ids")
    if linked is None and dep_col.get("value"):
        linked = [str(x["linkedPulseId"]) for x in json.loads(dep_col["value"]).get("linkedPulseIds", [])]
    priority = (col(mapping.priority_column).get("text") if mapping.priority_column else None) or "Medium"
    return {
        "task_id": f"{mapping.item_id_prefix}{item['id']}",
        "external_id": item["id"],
        "name": item["name"],
        "group_id": (item.get("group") or {}).get("id"),
        "status": mapping.status_labels[status_text],
        "owner_id": owner_id,
        "due_date": due,
        "priority": priority,
        "depends_on": [f"{mapping.item_id_prefix}{x}" for x in (linked or [])],
        "source": {"system": "monday", "board_id": board_id},
    }
