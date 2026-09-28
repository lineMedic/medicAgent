"""등록 서비스·설비·repo·알림 route catalog (W08: 조회 도구 범위, W22: repo·route·작성자).

- 조회 도구는 사건의 서비스에 등록된 설비·매뉴얼만, 배포 기록은 같은 라인의 등록 서비스만 보여 준다.
- 등록 repo·알림 route·자동 처리 작성자는 host 설정(config·env)에서만 온다. 모르는 repo·route,
  꺼진 route는 `CatalogError`로 거부한다. 모델·Issue 본문·로그가 repo·수신자를 지정하지 않는다.
- 자동 처리 작성자는 숫자 user ID로만 비교한다
  (login·`author_association` 문자열은 권한 근거가 아님).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from linemedic.common.config import (
    EquipmentConfig,
    LineMedicConfig,
    NotificationRoute,
    ServiceConfig,
)


class CatalogError(ValueError):
    """등록되지 않았거나 꺼진 repo·route."""


@dataclass(frozen=True)
class RepositoryEntry:
    id: int
    full_name: str
    service_id: str


@dataclass(frozen=True)
class Catalog:
    services: Mapping[str, ServiceConfig]
    equipment: Mapping[str, EquipmentConfig]
    repository: RepositoryEntry | None = None  # G2 전에는 없음
    routes: Mapping[str, NotificationRoute] = field(default_factory=dict)
    trusted_author_ids: frozenset[int] = frozenset()
    deny_labels: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, config: LineMedicConfig) -> "Catalog":
        repo = config.repository
        entry = None
        if repo.id is not None and repo.full_name is not None:
            entry = RepositoryEntry(repo.id, repo.full_name, repo.service_id)
        auto_start = config.issue_intake.auto_start
        return cls(
            services=dict(config.services),
            equipment=dict(config.equipment),
            repository=entry,
            routes=dict(config.notifications.routes),
            trusted_author_ids=frozenset(auto_start.author_ids or ()),
            deny_labels=frozenset(auto_start.deny_labels),
        )

    # 서비스·설비 (W08)

    def related_services(self, service: str) -> frozenset[str]:
        """같은 라인의 등록 서비스(자기 자신 포함)."""
        line = self.services[service].line_id if service in self.services else None
        same_line = {name for name, item in self.services.items() if item.line_id == line}
        return frozenset({service} | same_line)

    def equipment_of(self, service: str) -> dict[str, EquipmentConfig]:
        return {eid: item for eid, item in self.equipment.items() if item.service == service}

    def has_no_code(self, service: str) -> bool:
        """코드 경로가 없다고 등록된 서비스인가(`code_paths = []`, 설비 사건 등, D59·D89)."""
        item = self.services.get(service)
        return item is not None and item.code_paths == []

    def manuals_for(self, service: str) -> frozenset[str]:
        return frozenset(
            manual for item in self.equipment_of(service).values() for manual in item.manual_ref_ids
        )

    # repo·route·작성자 (W22)

    def require_repository(self, repository_id: int) -> RepositoryEntry:
        if self.repository is None:
            raise CatalogError("등록 repo가 설정되지 않았다(G2 전)")
        if repository_id != self.repository.id:
            raise CatalogError("등록되지 않은 repo")
        return self.repository

    def require_route(self, route_id: str) -> NotificationRoute:
        route = self.routes.get(route_id)
        if route is None:
            raise CatalogError("등록되지 않은 알림 route")
        if not route.enabled:
            raise CatalogError("꺼진 알림 route")
        return route

    def is_trusted_author(self, author_id: Any) -> bool:
        return (
            isinstance(author_id, int)
            and not isinstance(author_id, bool)
            and author_id in self.trusted_author_ids
        )
