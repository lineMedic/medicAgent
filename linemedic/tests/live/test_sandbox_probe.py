"""S3-C live 시험: 평가용 OpenShell sandbox 안 금지 요청·허용 경로·거절 근거 (W17, G5 필요).

`make test-live`에서만 실행된다. sandbox 구현(OpenShell `SandboxProbe`)이 없으면 통과가 아니라
skip(NOT_CONFIGURED)으로 보고한다. 결과는 `make security-test RUN_ID=`가
`runs/<run>/security/`에 남긴다.
"""

import pytest


@pytest.mark.live_sandbox
def test_sandbox_probe_denies_the_forbidden_request_with_a_policy_record():
    pytest.skip("NOT_CONFIGURED (G5): OpenShell SandboxProbe 구현이 아직 없다")
