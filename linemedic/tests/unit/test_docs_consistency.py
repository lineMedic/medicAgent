"""W29 문서 정합성 자동 점검: docs/09·STATUS·README가 구현과 맞는지 (docs/11 §1·§4, D93).

- docs/09가 가리키는 시험 파일이 모두 있다
- docs/09 표의 시험 ID는 가리킨 파일에 적혀 있다(`T-X-01·02` 같은 줄임 포함). 없는 것은
  hardening(H03~H07) 행뿐이다(core 완료 전에는 착수하지 않는다)
- STATUS 작업표에서 LIVE_VERIFIED로 올린 행은 실제로 있는 `evidence/` 원본을 가리킨다
- README와 제출 초안에 docs/11 §4의 금지 표현이 없다. 금지 목록은 docs/11 §4에 그대로 있어야 한다
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX = REPO_ROOT / "docs" / "09-test-matrix.md"
TESTS = REPO_ROOT / "linemedic" / "tests"
STATUS = REPO_ROOT / "STATUS.md"
DONE_RULES = REPO_ROOT / "docs" / "11-definition-of-done.md"
SUBMISSION_TEXTS = [REPO_ROOT / "README.md", REPO_ROOT / "docs" / "14-submission-draft.md"]
TEST_PATH = re.compile(r"`((?:unit|integration|live)/[A-Za-z0-9_./-]+\.py)`")
# docs/11 §4 '쓰지 않는 표현'의 핵심 문구. 문맥 조건이 붙은 표현도 제출 문서에서는 쓰지 않는다
BANNED = (
    "자동 복구 완료",
    "정비 완료",
    "사람이 읽음",
    "메일 배달됨",
    "모든 장애 해결",
    "NVIDIA embedding",
    "vector RAG",
    "실시간 Issue 감시",
    "온프레미스 추론",
    "두 runtime 사용",
    "조종당해도 무엇도 못 한다",
    "비밀이 전혀 없다",
    "모든 공격 차단",
    "PLC에 안전",
    "완전 폐쇄망",
    "무개입 자동화",
    "산업적 성공률",
    "MTTR 개선",
    "규칙 기반보다 우수",
)


def _ids_in(text: str) -> set[str]:
    """`T-AUTH-01·02·03`, `T-MEM-01~05` 같은 줄임을 펼친 시험 ID 집합."""
    found: set[str] = set()
    for prefix, first, rest in re.findall(r"(T-[A-Z0-9]+)-(\d{2})((?:[·~]\d{2})*)", text):
        numbers = [int(first)]
        for sep, number in re.findall(r"([·~])(\d{2})", rest):
            if sep == "~":
                numbers.extend(range(numbers[-1] + 1, int(number) + 1))
            else:
                numbers.append(int(number))
        found.update(f"{prefix}-{n:02d}" for n in numbers)
    return found


def _matrix_rows() -> list[tuple[str, str, str]]:
    rows = []
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and re.fullmatch(r"T-[A-Z0-9]+-\d{2}", cells[0]):
            match = TEST_PATH.search(cells[3])
            if match:
                rows.append((cells[0], match.group(1), cells[4]))
    return rows


def test_every_test_file_named_in_the_matrix_exists():
    paths = set(TEST_PATH.findall(MATRIX.read_text(encoding="utf-8")))
    assert paths, "docs/09에서 시험 파일을 찾지 못했다"
    assert sorted(p for p in paths if not (TESTS / p).is_file()) == []


def test_every_matrix_id_is_in_its_file_or_is_hardening():
    rows = _matrix_rows()
    assert len(rows) >= 40
    missing = [
        (tid, card)
        for tid, path, card in rows
        if tid not in _ids_in((TESTS / path).read_text(encoding="utf-8"))
    ]
    assert missing, "hardening 행이 모두 구현됐다면 이 기대를 고친다"
    assert [(tid, card) for tid, card in missing if not re.fullmatch(r"H0[3-7]", card)] == []


def test_id_shorthand_is_expanded():
    assert _ids_in("(T-AUTH-01·02·03)") == {"T-AUTH-01", "T-AUTH-02", "T-AUTH-03"}
    assert _ids_in("T-MEM-01~03, T-V4-02") == {"T-MEM-01", "T-MEM-02", "T-MEM-03", "T-V4-02"}


def test_live_verified_rows_cite_existing_evidence():
    in_table = False
    for line in STATUS.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 작업표"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not in_table or len(cells) < 6 or not cells[3].startswith("LIVE_VERIFIED"):
            continue
        cited = re.findall(r"`?(evidence/[A-Za-z0-9_./-]+)`?", cells[4])
        assert cited, f"{cells[1]}: LIVE_VERIFIED인데 evidence 원본 경로가 없다"
        assert [p for p in cited if not (REPO_ROOT / p).exists()] == [], cells[1]


def test_banned_phrases_are_the_ones_in_docs_11():
    section = DONE_RULES.read_text(encoding="utf-8").split("## 4.", 1)[1].split("\n## ", 1)[0]
    assert [phrase for phrase in BANNED if phrase not in section] == []


@pytest.mark.parametrize("path", SUBMISSION_TEXTS, ids=lambda p: p.name)
def test_submission_texts_have_no_banned_phrases(path):
    if not path.exists():
        pytest.skip(f"{path.name} 없음")
    text = path.read_text(encoding="utf-8")
    assert [phrase for phrase in BANNED if phrase in text] == []
