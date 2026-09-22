from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


SaleStatus = Literal["销售中", "未开售", "待开售", "暂停", "停售", "待核实"]
LineupStatus = Literal["未请求", "预计", "已确认", "待核实", "无法获取"]


class OfficialPlay(BaseModel):
    status: SaleStatus = "待核实"
    home: float | None = None
    draw: float | None = None
    away: float | None = None


class Fixture(BaseModel):
    sequence: int = Field(ge=1)
    match_id: str
    match_number: str
    business_date: str
    competition: str
    home_team: str
    away_team: str
    kickoff_time: datetime
    official_handicap: int | None = None
    neutral: bool = False
    result_play: OfficialPlay = Field(default_factory=OfficialPlay)
    handicap_play: OfficialPlay = Field(default_factory=OfficialPlay)
    lineup_status: LineupStatus = "未请求"
    source_snapshot_id: str | None = None


class FixtureBatch(BaseModel):
    fixtures: list[Fixture]

    @model_validator(mode="after")
    def preserve_unique_order(self) -> "FixtureBatch":
        if [item.sequence for item in self.fixtures] != list(range(1, len(self.fixtures) + 1)):
            raise ValueError("sequence必须为1..N且保持官方顺序")
        ids = [item.match_id for item in self.fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("match_id不得重复")
        return self


class DailyPredictionRequest(BaseModel):
    fixtures: list[Fixture]
    model_version: str = "auto"
    rule_budget: int = Field(default=6, ge=1, le=20)


class ResultInput(BaseModel):
    match_id: str
    home_goals_90: int | None = Field(default=None, ge=0)
    away_goals_90: int | None = Field(default=None, ge=0)
    status: Literal["已完赛", "待确认", "延期", "中止", "取消", "待补赛"]
    source: str | None = None
    verified_90_minutes: bool = False
    official_settlement_verified: bool = False


class ReviewRequest(BaseModel):
    prediction_path: str
    results: list[ResultInput]
