# S1b 거짓 정상 이미지 (trusted harness 전용, spec 08 §7·09 §4)
# 신뢰 레시피(linemedic/runner/mes.Dockerfile)로 만든 MES 이미지 위에 잘못된 집계 구현 하나만 덮어쓴다.
# 제품 broker·/tools에는 이 이미지를 배포하는 경로가 없다. origin=human_injected_negative.
ARG MES_IMAGE=linemedic-mes:base
FROM ${MES_IMAGE}
COPY wrong_200_defects.py /srv/app/defects.py
