"""TAWOS adapter: a project export from dump.py/extract.py -> Nabu canonical JSONL.

Ported from Nabu 1 (github.com/Kang-Ji-2048/Nabu, ad45462): dump.py, extract.py and
mappings.json are copied unchanged; timestamp() and event_kind() come from its pipeline.py.

Each TAWOS project becomes one scope. Epic membership has no column in TAWOS, so it
comes from 'Epic Link' (on the child) and 'Epic Child' (on the epic) change events:
the last observation wins, and parent_at records when the ticket joined its epic.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CANCELLED_RESOLUTIONS = {"won't fix", "won't do", "duplicate", "invalid", "incomplete", "cannot reproduce",
                         "not a problem", "abandoned", "unresolved"}


def timestamp(value):
    if value is None or value == "":
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # TAWOS documents all its timezone-naive timestamps as UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def event_kind(field):
    # TAWOS Change_Type is broader than a single field: STATUS includes
    # resolution, and STORY_POINT includes actual points recorded afterwards.
    return {
        "status": "status_changed", "resolution": "resolution_changed",
        "story points": "estimate_changed", "story point": "estimate_changed",
        "actual story points": "actual_estimate_changed",
        "summary": "text_changed", "description": "text_changed",
        "assignee": "people_changed", "reporter": "people_changed",
        "epic link": "membership_changed", "epic child": "membership_changed",
        "sprint": "membership_changed",
    }.get(field.casefold(), "field_changed")


def convert(export: Path, output: Path, tenant: str = "tawos", mapping_file: Path | None = None) -> dict:
    mappings = json.loads((mapping_file or Path(__file__).with_name("mappings.json")).read_text(encoding="utf-8"))
    tables = defaultdict(list)
    with open(export, encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                tables[record["table"]].append(record["row"])

    report = Counter()
    projects = {p["ID"]: p["Project_Key"] for p in tables["Project"]}
    issues = {i["ID"]: i for i in tables["Issue"] if i.get("Issue_Key") and i.get("Creation_Date")}
    report["skipped_issues"] = len(tables["Issue"]) - len(issues)
    key_to_id = {i["Issue_Key"]: i["ID"] for i in issues.values()}
    nid = lambda issue: f"{tenant}:{issue['Issue_Key']}"
    scope = lambda issue: projects.get(issue["Project_ID"], "unknown")

    components = {c["ID"]: c["Name"] for c in tables["Component"]}
    issue_components = defaultdict(list)
    for row in tables["Issue_Component"]:
        if row["Component_ID"] in components:
            issue_components[row["Issue_ID"]].append(components[row["Component_ID"]])

    changes = defaultdict(list)
    for row in tables["Change_Log"]:
        if row.get("Issue_ID") in issues and row.get("Field") and row.get("Creation_Date"):
            changes[row["Issue_ID"]].append({**row, "at": timestamp(row["Creation_Date"])})

    # Epic membership: (at, child issue ID, epic issue ID or None for removal).
    membership = []
    for issue_id, rows in changes.items():
        for row in rows:
            field = row["Field"].casefold()
            if field == "epic link":
                membership.append((row["at"], issue_id, key_to_id.get(row.get("To_String") or "")))
            elif field == "epic child":
                if row.get("To_String") in key_to_id:
                    membership.append((row["at"], key_to_id[row["To_String"]], issue_id))
                if row.get("From_String") in key_to_id and not row.get("To_String"):
                    membership.append((row["at"], key_to_id[row["From_String"]], None))
    # Jira logs link changes as Field 'Link', e.g. To_String "This issue is blocked by XD-12".
    linked_at = {}
    for issue_id, rows in changes.items():
        for row in rows:
            if row["Field"].casefold() == "link" and row.get("To_String"):
                other = key_to_id.get(row["To_String"].split()[-1])
                if other:
                    pair = frozenset((issue_id, other))
                    linked_at[pair] = min(linked_at.get(pair, row["at"]), row["at"])
    parent = {}
    for at, child, epic in sorted(membership, key=lambda m: m[0]):
        if epic is None:
            parent.pop(child, None)
        elif parent.get(child, (None, None))[0] != epic:
            parent[child] = (epic, at)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        write = lambda table, row: out.write(json.dumps({"table": table, "row": row}) + "\n")
        for issue_id, issue in issues.items():
            status = mappings["statuses"].get((issue.get("Status") or "").casefold())
            if status is None:
                report["unmapped_status:" + str(issue.get("Status"))] += 1
                status = "done" if issue.get("Resolution_Date") else "todo"
            if status == "done" and (issue.get("Resolution") or "").casefold() in CANCELLED_RESOLUTIONS:
                status = "cancelled"
            item_type = mappings["types"].get((issue.get("Type") or "").casefold())
            if item_type is None:
                report["unmapped_type:" + str(issue.get("Type"))] += 1
                item_type = "task"
            started = min((row["at"] for row in changes[issue_id] if row["Field"].casefold() == "status"
                           and mappings["statuses"].get((row.get("To_String") or "").casefold()) == "in_progress"),
                          default=None)
            epic = parent.get(issue_id)
            write("work_item", {
                "id": nid(issue), "tenant_id": tenant, "scope": scope(issue), "source": "tawos",
                "source_key": issue["Issue_Key"], "type": item_type, "title": issue.get("Title") or "",
                "description": issue.get("Description_Text") or issue.get("Description"),
                "parent_id": nid(issues[epic[0]]) if epic and issues[epic[0]] is not issue else None,
                "parent_at": epic[1] if epic else None,
                "components": issue_components.get(issue_id, []), "labels": [],
                "estimate": issue.get("Story_Point"), "estimate_unit": "points" if issue.get("Story_Point") is not None else None,
                "status_category": status, "created_at": timestamp(issue["Creation_Date"]), "started_at": started,
                "resolved_at": timestamp(issue.get("Resolution_Date")), "due_at": None,
                "raw": {"url": issue.get("URL"), "priority": issue.get("Priority"), "resolution": issue.get("Resolution")}})
            report["work_items"] += 1
            report["with_epic"] += int(bool(epic))
            for row in changes[issue_id]:
                write("event", {"item_id": nid(issue), "at": row["at"], "kind": event_kind(row["Field"]),
                                "from_value": row.get("From_String"), "to_value": row.get("To_String"),
                                "tenant_id": tenant, "scope": scope(issue)})
                report["events"] += 1
        seen = set()
        for row in tables["Issue_Link"]:
            source, target = issues.get(row.get("Issue_ID")), issues.get(row.get("Target_Issue_ID"))
            mapping = mappings["link_descriptions"].get((row.get("Description") or "").casefold())
            if not (source and target and mapping):
                report["skipped_links"] += 1
                continue
            origin, dest = (target, source) if mapping["reverse"] else (source, target)
            edge = (nid(origin), nid(dest), mapping["kind"])
            if edge in seen:  # TAWOS stores each link from both ends
                continue
            seen.add(edge)
            # Without a logged 'Link' change, a link counts as known once both tickets exist.
            write("link", {"from_id": edge[0], "to_id": edge[1], "kind": edge[2], "tenant_id": tenant,
                           "scope": scope(dest), "created_at": linked_at.get(frozenset((source["ID"], target["ID"])))})
            report["links"] += 1
    return dict(report)
