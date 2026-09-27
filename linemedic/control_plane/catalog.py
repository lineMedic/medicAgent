"""등록 서비스·설비 catalog (W08: 조회 도구 범위. repo·알림 route catalog는 W22에서 더한다).

조회 도구는 사건의 서비스에 등록된 설비·매뉴얼만, 배포 기록은 같은 라인의 등록 서비스만 보여 준다.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from linemedic.common.config import EquipmentConfig, LineMedicConfig, ServiceConfig


@dataclass(frozen=True)
class Catalog:
    services: Mapping[str, ServiceConfig]
    equipment: Mapping[str, EquipmentConfig]

    @classmethod
    def from_config(cls, config: LineMedicConfig) -> "Catalog":
        return cls(dict(config.services), dict(config.equipment))

    def related_services(self, service: str) -> frozenset[str]:
        """같은 라인의 등록 서비스(자기 자신 포함)."""
        line = self.services[service].line_id if service in self.services else None
        same_line = {name for name, item in self.services.items() if item.line_id == line}
        return frozenset({service} | same_line)

    def equipment_of(self, service: str) -> dict[str, EquipmentConfig]:
        return {eid: item for eid, item in self.equipment.items() if item.service == service}

    def manuals_for(self, service: str) -> frozenset[str]:
        return frozenset(
            manual for item in self.equipment_of(service).values() for manual in item.manual_ref_ids
        )
