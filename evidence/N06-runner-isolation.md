# N06 runner 부분: 격리 runner 네트워크·마운트·자원 제한

- 실행 시각(UTC): 2026-09-27T12:22:45.186017Z
- 실행 환경: 로컬 개발 Mac(macOS-26.6.2-arm64-arm-64bit), Docker server 28.1.1. 데모 호스트(G1) 아님
- 명령: `LINEMEDIC_RECORD_EVIDENCE=1 make test-docker` (`linemedic/tests/integration/test_runner_docker.py`)
- runner image: `sha256:262eec28e03d17f90cc0eb5de722948b08854aea28a9bab83aeea51020a35d3d` (`linemedic/runner/runner.Dockerfile`)
- 결과: PASS (컨테이너 안 probe 6개 모두 통과, exit 0)
  - `test_no_external_connection`: PASSED
  - `test_no_dns`: PASSED
  - `test_repo_and_root_filesystem_are_read_only`: PASSED
  - `test_only_tmp_is_writable`: PASSED
  - `test_runs_as_fixed_non_root_user`: PASSED
  - `test_no_docker_socket`: PASSED
- docker inspect(실제 적용 값):

```json
{
  "cap_add": null,
  "cap_drop": [
    "ALL"
  ],
  "image": "sha256:262eec28e03d17f90cc0eb5de722948b08854aea28a9bab83aeea51020a35d3d",
  "ipc_mode": "private",
  "memory": 536870912,
  "memory_swap": 536870912,
  "mounts": [
    {
      "destination": "/work/repo",
      "rw": false,
      "type": "bind"
    },
    {
      "destination": "/work/results",
      "rw": true,
      "type": "bind"
    }
  ],
  "nano_cpus": 1000000000,
  "network_mode": "none",
  "pid_mode": "",
  "pids_limit": 64,
  "privileged": false,
  "read_only_rootfs": true,
  "security_opt": [
    "no-new-privileges"
  ],
  "tmpfs": {
    "/tmp": "rw,noexec,nosuid,nodev,size=64m"
  },
  "user": "10001:10001"
}
```

- 한계: 같은 Python 프로세스의 비신뢰 코드는 결과 파일을 조작할 수 있다. 테스트 PASS는 악성 코드가 없다는 뜻이 아니다. 배포 MES의 네트워크 부분(N06 나머지)은 W12에서 확인한다
