"""테스트·demo 전용: 시험 사건을 VERIFYING으로 준비한다 (spec 08 §7, W05 2부).

제품 전이 경로와 운영 API에는 임의 상태를 정하는 함수가 없다.
이 도우미는 테스트와 trusted harness(S1b, `make verify-negative`)만 쓴다.
`control_plane` 모듈이 이 파일을 쓰지 않는지 정적 검사 테스트가 확인한다.
준비 사실은 `DEMO_STATE_PREPARED` 감사 기록(actor `trusted_harness`)으로 남긴다. 이 사건을
"에이전트가 고쳐서 브로커까지 통과한 사건"으로 표시하지 않는다.
"""

from linemedic.common.canonical_json import canonical_dumps
from linemedic.common.ids import new_id
from linemedic.control_plane import audit
from linemedic.control_plane.store import Tx

HARNESS_ACTOR = "trusted_harness"


def prepare_verifying_incident(
    tx: Tx,
    run_id: str,
    *,
    purpose: str,
    fingerprint: str,
    repository_id: int = 0,
    service: str = "mes-api",
    line_id: str = "L3",
) -> str:
    """VERIFYING 상태의 사건을 넣고 ID를 돌려준다. work·attempt·Issue 연결은 만들지 않는다."""
    if tx.one("SELECT 1 FROM demo_runs WHERE id = ?", (run_id,)) is None:
        raise ValueError(f"run이 제어 DB에 없다: {run_id}")
    incident_id = new_id("INC")
    tx.execute(
        "INSERT INTO incidents(id, run_id, routing_scope, repository_id, fingerprint,"
        " fingerprint_version, source_kind, status, service, line_id, first_seen, last_seen,"
        " details_json) VALUES (?, ?, ?, ?, ?, 'v1', 'OPERATOR', 'VERIFYING', ?, ?, ?, ?, ?)",
        (
            incident_id,
            run_id,
            f"eval:{run_id}",
            repository_id,
            fingerprint,
            service,
            line_id,
            tx.now,
            tx.now,
            canonical_dumps({"prepared_by": HARNESS_ACTOR, "purpose": purpose}),
        ),
    )
    audit.append(
        tx,
        run_id,
        incident_id,
        HARNESS_ACTOR,
        "DEMO_STATE_PREPARED",
        {"status": "VERIFYING", "purpose": purpose},
    )
    return incident_id
