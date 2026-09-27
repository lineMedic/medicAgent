"""보호된 회귀 시험: 정상 로트와 빈 로트의 집계."""

from app.defects import summarize
from app.main import app
from fastapi.testclient import TestClient


def test_normal_lot_summary(monkeypatch):
    monkeypatch.delenv("MES_DATA_DIR", raising=False)  # 저장소의 data/ 로트를 읽는다
    response = TestClient(app).get("/defects/summary", params={"lot_id": "L3-0927-101"})
    assert response.status_code == 200
    assert response.json() == {
        "lot_id": "L3-0927-101",
        "total_defects": 3,
        "by_inspector": {"I-01": 2, "I-02": 1},
    }


def test_empty_lot_summary():
    assert summarize("L3-EMPTY-000", []) == {
        "lot_id": "L3-EMPTY-000",
        "total_defects": 0,
        "by_inspector": {},
    }


def test_unknown_lot_is_404(monkeypatch, tmp_path):
    (tmp_path / "lots").mkdir()
    monkeypatch.setenv("MES_DATA_DIR", str(tmp_path))
    response = TestClient(app).get("/defects/summary", params={"lot_id": "L3-0000-000"})
    assert response.status_code == 404


def test_healthz():
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}
