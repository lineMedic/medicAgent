"""허용 매뉴얼 조회와 정비 초안 승인 문구 (W08, spec 03 §2·§3.B, D58·D60).

- 팀이 만든 가상 매뉴얼(`linemedic/factory_sim/manuals/<manual_ref_id>.md`)만 읽는다.
  첫 줄은 "실제 산업 매뉴얼·안전 절차가 아님" 안내이고, 본문은 `## <절 ID> <제목>` 단위로 나눈다.
- 검색은 대소문자 무시 부분 문자열이다. URL fetch·정규식·파일 경로 입력이 없다.
- 승인 문구(`linemedic/policies/manual_templates.toml`)는 매뉴얼과 절 ID가 실제로 있는지
  확인해 읽는다.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from linemedic.common.config import read_toml, validate_model

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUALS_DIR = REPO_ROOT / "linemedic" / "factory_sim" / "manuals"
TEMPLATES_PATH = REPO_ROOT / "linemedic" / "policies" / "manual_templates.toml"
MANUAL_ID_RE = re.compile(r"^MANUAL-[A-Z0-9][A-Z0-9.-]{0,47}$")
SECTION_RE = re.compile(r"^## (\d+(?:\.\d+)+) (.+)$")
DISCLAIMER_MARK = "실제 산업 매뉴얼·안전 절차가 아님"
MAX_RESULTS = 5


class KnowledgeError(ValueError):
    """매뉴얼·승인 문구 파일이 규칙에 맞지 않음."""


@dataclass(frozen=True)
class ManualSection:
    manual_ref_id: str
    section_id: str
    title: str
    text: str


@dataclass(frozen=True)
class Manual:
    manual_ref_id: str
    disclaimer: str
    sections: tuple[ManualSection, ...]


def load_manual(path: Path) -> Manual:
    manual_ref_id = path.stem
    if not MANUAL_ID_RE.fullmatch(manual_ref_id):
        raise KnowledgeError(f"매뉴얼 파일 이름이 manual_ref_id 형식이 아니다: {path.name}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or DISCLAIMER_MARK not in lines[0]:
        raise KnowledgeError(f"{path.name}: 첫 줄에 가상 매뉴얼 안내가 없다")
    disclaimer = lines[0].lstrip("> ").strip()
    sections, current, body = [], None, []
    for line in lines[1:]:
        match = SECTION_RE.match(line)
        if match:
            if current is not None:
                sections.append(ManualSection(manual_ref_id, *current, "\n".join(body).strip()))
            current, body = (match.group(1), match.group(2).strip()), []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections.append(ManualSection(manual_ref_id, *current, "\n".join(body).strip()))
    if not sections:
        raise KnowledgeError(f"{path.name}: 절(## <절 ID> <제목>)이 없다")
    return Manual(manual_ref_id, disclaimer, tuple(sections))


class KnowledgeBase:
    def __init__(self, manuals_dir: Path = MANUALS_DIR) -> None:
        self.manuals = {
            manual.manual_ref_id: manual
            for manual in (load_manual(path) for path in sorted(manuals_dir.glob("*.md")))
        }

    def search(
        self, manual_ref_ids: Iterable[str], q: str | None, limit: int = MAX_RESULTS
    ) -> list[tuple[Manual, ManualSection]]:
        """허용된 매뉴얼의 절 중 q를 포함하는 것(q가 없으면 앞에서부터)."""
        needle = q.casefold() if q else None
        found = []
        for manual_ref_id in sorted(set(manual_ref_ids)):
            manual = self.manuals.get(manual_ref_id)
            if manual is None:
                continue
            for section in manual.sections:
                haystack = f"{section.section_id} {section.title}\n{section.text}".casefold()
                if needle is None or needle in haystack:
                    found.append((manual, section))
        return found[:limit]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ManualTemplate(_Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    section_ids: Annotated[list[str], Field(min_length=1)]
    guidance: Annotated[str, Field(min_length=1, max_length=1000)]
    disclaimer: Annotated[str, Field(min_length=1, max_length=500)]


class ManualTemplates(_Model):
    templates: dict[str, ManualTemplate]


def load_manual_templates(
    knowledge: KnowledgeBase, path: Path = TEMPLATES_PATH
) -> dict[str, ManualTemplate]:
    """승인 문구를 읽고, 가리키는 매뉴얼·절이 실제로 있는지 확인한다."""
    templates = validate_model(ManualTemplates, read_toml(path), "manual templates").templates
    for manual_ref_id, template in templates.items():
        manual = knowledge.manuals.get(manual_ref_id)
        if manual is None:
            raise KnowledgeError(f"승인 문구가 없는 매뉴얼을 가리킨다: {manual_ref_id}")
        known = {section.section_id for section in manual.sections}
        missing = set(template.section_ids) - known
        if missing:
            raise KnowledgeError(f"{manual_ref_id}: 없는 절 {sorted(missing)}")
    return templates
