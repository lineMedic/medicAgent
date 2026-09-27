# LineMedic 신뢰 MES 빌드 레시피 (W04, spec 07 §5)
# - 데모 repo 안의 Dockerfile·setup script·requirements 파일을 쓰지 않는다. 의존성은 이 파일에 고정한다.
# - 빌드 context(시드 또는 candidate checkout)에서 app/만 복사한다.
# - 데이터는 실행 시 /data에 read-only로 mount한다(MES_DATA_DIR=/data).
# - base image는 태그로 받고, 빌드 뒤 image ID·digest를 기록한다(make mes-image 출력).
# - 비루트 사용자로 실행하고, stdout에는 앱의 JSON 로그만 남긴다(uvicorn access log 끔).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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
        uvicorn==0.54.0 \
        click==8.5.0 \
        h11==0.16.0 \
    && pip check \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mes

WORKDIR /srv
COPY app/ /srv/app/
ENV MES_DATA_DIR=/data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-level", "warning"]
