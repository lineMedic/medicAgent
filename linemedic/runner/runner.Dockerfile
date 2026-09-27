# LineMedic 고정 runner image 레시피 (W10, spec 06 §4·§5, docs/07 §2)
# - 데모 repo의 Dockerfile·requirements·pytest 설정을 쓰지 않는다. MES 의존성과 pytest를 이 파일에 고정한다.
# - 검사할 코드는 넣지 않는다. 실행 때 서버가 만든 tree를 /work/repo에 read-only로 mount한다.
# - 보호 pytest 설정을 image에 넣는다. RUNNER_IMAGE_ID(image ID)가 설정까지 고정한다.
# - 비루트 사용자(10001)로 실행한다. network·capability·자원 제한은 runner가 docker run 옵션으로 건다.
# - 빌드 context는 linemedic/runner/다(make runner-image).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-deps \
        fastapi==0.141.1 \
        starlette==1.7.0 \
        pydantic==2.13.5 \
        pydantic_core==2.46.5 \
        annotated-types==0.8.0 \
        annotated-doc==0.0.5 \
        typing_extensions==4.16.0 \
        typing-inspection==0.4.4 \
        anyio==4.15.1 \
        idna==3.20 \
        h11==0.16.0 \
        httpx==0.28.1 \
        httpcore==1.0.9 \
        certifi==2026.7.22 \
        pytest==9.1.1 \
        pluggy==1.6.0 \
        iniconfig==2.3.0 \
        packaging==26.3 \
        Pygments==2.21.0 \
    && pip check \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

COPY pytest-protected.ini /opt/linemedic/pytest-protected.ini
WORKDIR /work/repo
USER 10001:10001
