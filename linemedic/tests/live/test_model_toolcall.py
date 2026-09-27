"""N01 live 시험: 실제 NVIDIA endpoint로 tool 선택 → 결과 재입력 → 제안 (W02·W14, G3 필요).

`make test-live`에서만 실행된다. 필수 env가 없으면 통과가 아니라 skip(NOT_CONFIGURED)으로 보고한다.
통과하면 결과를 evidence/spikes/에 기록한다(tasks/W02 참고).
"""

import pytest

from linemedic.common.config import process_env
from linemedic.scripts.spikes import n01_model_tool_call as n01


@pytest.mark.live_model
def test_model_tool_call_roundtrip():
    env = process_env()
    missing = n01.missing_env(env)
    if missing:
        pytest.skip(f"NOT_CONFIGURED (G3): {', '.join(missing)}")
    record = n01.run_spike(env)
    assert record["verdict"] == "PASS", record["reason"]
