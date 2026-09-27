"""제안·응답 JSON Schema 생성 (W09, spec 03 머리말, docs/02 `contracts/api/`).

pydantic 모델이 원본이다. `make api-schema`가 `linemedic/contracts/api/*.schema.json`을 다시 쓰고,
`--check`는 파일이 모델과 다르면 종료 코드 1을 돌려준다(단위 테스트도 같은 비교를 한다).
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from linemedic.control_plane.broker.proposals import Proposal, ProposalReceipt, ProposalStatus

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "contracts" / "api"
SCHEMAS: dict[str, type[BaseModel]] = {
    "proposal.schema.json": Proposal,
    "proposal-receipt.schema.json": ProposalReceipt,
    "proposal-status.schema.json": ProposalStatus,
}


def render(name: str, model: type[BaseModel]) -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:linemedic:v4:{name.removesuffix('.schema.json')}",
        **model.model_json_schema(),
    }
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def stale_files(directory: Path = SCHEMA_DIR) -> list[str]:
    """모델과 내용이 다른(또는 없는) schema 파일 이름."""
    stale = []
    for name, model in SCHEMAS.items():
        path = directory / name
        if not path.is_file() or path.read_text(encoding="utf-8") != render(name, model):
            stale.append(name)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api_schema")
    parser.add_argument("--check", action="store_true", help="파일이 모델과 같은지 확인만 한다")
    parser.add_argument("--dir", type=Path, default=SCHEMA_DIR)
    args = parser.parse_args(argv)
    if args.check:
        stale = stale_files(args.dir)
        if stale:
            print(f"JSON Schema가 모델과 다르다(make api-schema): {', '.join(stale)}")
            return 1
        print("JSON Schema 최신")
        return 0
    args.dir.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        (args.dir / name).write_text(render(name, model), encoding="utf-8")
        print(f"썼다: {args.dir / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
