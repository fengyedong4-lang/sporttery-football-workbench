from pathlib import Path

from app.services.model import export_training_csv, import_football_data_csv, import_training_csv


def test_football_data_adapter_keeps_result_source(tmp_path: Path):
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n01/08/2024,A,B,2,1,H\n",
        encoding="utf-8",
    )
    rows = import_football_data_csv(raw, competition="L", source_url="https://example.test/data.csv")
    assert len(rows) == 1
    assert rows[0].source == "https://example.test/data.csv"
    normalized = tmp_path / "clean.csv"
    export_training_csv(rows, normalized)
    reloaded = import_training_csv(normalized)
    assert reloaded[0].home_goals == 2
