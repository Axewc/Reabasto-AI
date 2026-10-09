from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings read from the environment / .env (secrets live here only)."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: str = "fake"
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    gateway_base_url: str = ""
    gateway_api_key: str = ""
    gcp_project: str = ""
    bq_dataset: str = "reabasto"
    engine: str = "duckdb"
    api_keys: str = ""
    sqlite_path: str = "data/reabasto.sqlite"
    mlflow_tracking_uri: str = "sqlite:///mlflow.db"


class FamilyAssumptions(BaseModel):
    perishable: bool
    unit_cost_mxn: float
    holding_cost_daily_pct: float
    stockout_margin_mxn: float


class Split(BaseModel):
    train_start: date
    train_end: date
    valid_start: date
    valid_end: date
    sim_start: date
    sim_end: date


class Assumptions(BaseModel):
    horizon: dict
    service_level: float
    families: dict[str, FamilyAssumptions]
    simulation: dict
    split: Split

    def lead_time(self, perishable: bool) -> int:
        key = "perishable" if perishable else "non_perishable"
        return int(self.horizon["lead_time_days"][key])

    def horizon_days(self, perishable: bool) -> int:
        """H = L + R, the window whose total demand the model forecasts."""
        return self.lead_time(perishable) + int(self.horizon["review_days"])


def load_assumptions(path: Path | None = None) -> Assumptions:
    path = path or ROOT / "config" / "assumptions.yaml"
    return Assumptions.model_validate(yaml.safe_load(path.read_text()))
