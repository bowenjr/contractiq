"""Validated service boundary for TASK-10 scope and interface workflows."""

from datetime import date

from core.scope_gap_rules import calculate_coverage, evaluate_gaps
from core.scope_interfaces import InterfaceRecord, ScopeItem
from core.scope_repository import ScopeInterfaceRepository


class ScopeInterfaceService:
    def __init__(self, repository: ScopeInterfaceRepository) -> None:
        self.repository = repository

    def create_scope_item(
        self, item: ScopeItem, actor: str, requirement_ids: list[str] | None = None
    ) -> ScopeItem:
        self.repository.create_scope_item(item, actor, requirement_ids)
        return item

    def create_interface(
        self, record: InterfaceRecord, actor: str, scope_item_ids: list[str] | None = None
    ) -> InterfaceRecord:
        self.repository.create_interface(record, actor, scope_item_ids)
        return record

    def withdraw_scope_item(self, scope_item_id: str, expected_version: int, actor: str) -> None:
        self.repository.withdraw_scope_item(scope_item_id, expected_version, actor)

    def withdraw_interface(self, interface_id: str, expected_version: int, actor: str) -> None:
        self.repository.withdraw_interface(interface_id, expected_version, actor)

    def projection(self, bid_id: str, as_of_date: date) -> object:
        scopes = self.repository.list_scope_items(bid_id)
        interfaces = self.repository.list_interfaces(bid_id)
        links = self.repository.requirement_links()
        scope_links: dict[str, list[str]] = {}
        for requirement_id, scope_id in links:
            scope_links.setdefault(scope_id, []).append(requirement_id)
        interface_links: dict[str, list[str]] = {}
        for interface_id, scope_id in self.repository.interface_scope_links():
            interface_links.setdefault(interface_id, []).append(scope_id)
        combined = {**scope_links, **interface_links}
        gaps = evaluate_gaps(scopes, interfaces, {}, combined, {}, as_of_date)
        return calculate_coverage(scopes, interfaces, gaps)
