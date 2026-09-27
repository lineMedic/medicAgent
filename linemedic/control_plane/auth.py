"""token → principal (W06, spec 07 §3, docs/04 §1).

- `/tools/*`는 `AgentPrincipal`(run·incident·work·attempt 범위),
  `/ops/*`는 `OperatorPrincipal`만 쓴다.
- token은 SHA-256 hash로만 보관·비교한다. 원문 token은 발급 순간에만 호출자에게 돌려준다.
- agent token은 host가 attempt를 만들 때 발급하고 attempt가 끝나면 `revoke_attempt`로 폐기한다.
- 권한은 인증된 principal로만 판단한다. 요청 body의 actor·role 값으로 판단하지 않는다.

P0에서는 한 프로세스(`make start`, W13)가 API와 supervisor를 함께 돌리므로 등록부를 메모리에 둔다.
프로세스가 다시 시작되면 agent token은 모두 무효가 된다.
진행 중 attempt도 자동 재개하지 않는다(spec 04 §8).
"""

import hashlib
import secrets
import threading
from dataclasses import dataclass
from typing import Any

from linemedic.common.ids import is_valid_entity_id
from linemedic.control_plane.errors import ApiError
from linemedic.control_plane.store import Tx

MIN_TOKEN_LENGTH = 32
OPERATOR_ROLES = frozenset(
    {
        "read",
        "operate",
        "triage",
        "authorize",
        "approve",
        "reconcile",
        "maintenance",
        "demo",
        "integration",
    }
)


@dataclass(frozen=True)
class AgentPrincipal:
    run_id: str
    incident_id: str
    work_id: str
    attempt_id: str

    @property
    def scope(self) -> str:
        return f"agent:{self.run_id}:{self.incident_id}:{self.work_id}:{self.attempt_id}"


@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: str
    roles: frozenset[str]

    @property
    def scope(self) -> str:
        return f"operator:{self.operator_id}"


Principal = AgentPrincipal | OperatorPrincipal


class AuthError(Exception):
    """token 등록·발급을 할 수 없음."""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenRegistry:
    """token hash → principal. 원문 token은 저장하지 않는다."""

    def __init__(self) -> None:
        self._by_hash: dict[str, Principal] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"TokenRegistry(tokens={len(self._by_hash)})"

    def register_operator(self, token: str, principal: OperatorPrincipal) -> None:
        if len(token) < MIN_TOKEN_LENGTH:
            raise AuthError(f"operator token은 {MIN_TOKEN_LENGTH}자 이상이어야 한다")
        unknown = principal.roles - OPERATOR_ROLES
        if unknown:
            raise AuthError(f"알 수 없는 operator 역할: {sorted(unknown)}")
        with self._lock:
            self._by_hash[token_hash(token)] = principal

    def issue_agent_token(self, principal: AgentPrincipal) -> str:
        """attempt용 agent token을 새로 만든다. 원문은 이 반환값뿐이다."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._by_hash[token_hash(token)] = principal
        return token

    def revoke_attempt(self, attempt_id: str) -> int:
        with self._lock:
            revoked = [
                digest
                for digest, principal in self._by_hash.items()
                if isinstance(principal, AgentPrincipal) and principal.attempt_id == attempt_id
            ]
            for digest in revoked:
                del self._by_hash[digest]
        return len(revoked)

    def resolve(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._lock:
            return self._by_hash.get(token_hash(token))


def bearer_token(authorization: str | None) -> str | None:
    """`Authorization: Bearer <token>`에서 token을 꺼낸다. 형식이 아니면 None."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value or " " in value:
        return None
    return value


def host_operator(token: str) -> tuple[str, OperatorPrincipal]:
    """env `CONTROL_OPERATOR_TOKEN`의 호스트 운영자. P0는 모든 운영 역할을 가진 한 명이다."""
    return token, OperatorPrincipal(operator_id="host-operator", roles=OPERATOR_ROLES)


def can_access_incident(principal: Principal, incident: Any) -> bool:
    """사건 조회 범위. operator는 모든 사건, agent는 자기 run·incident만.

    거부할 때 호출자는 '없음'과 같은 응답을 돌려 존재 여부를 드러내지 않는다(T-AUTH-02).
    """
    if isinstance(principal, OperatorPrincipal):
        return "read" in principal.roles
    return (incident["run_id"], incident["id"]) == (principal.run_id, principal.incident_id)


def load_visible_incident(tx: Tx, principal: Principal, incident_id: str) -> Any:
    """principal이 볼 수 있는 사건만 읽는다. 형식 오류·없음·범위 밖은 모두 같은 404다."""
    if not is_valid_entity_id(incident_id, "INC"):
        raise ApiError("RESOURCE_NOT_FOUND")
    incident = tx.one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if incident is None or not can_access_incident(principal, incident):
        raise ApiError("RESOURCE_NOT_FOUND")
    return incident
