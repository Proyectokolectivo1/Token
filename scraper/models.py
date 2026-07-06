"""
Modelos de datos Pydantic para el output de cada grafo de scraping.
Estos esquemas son los que ScrapegraphAI usa para forzar JSON schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Gender(str, Enum):
    female = "female"
    male = "male"
    couple = "couple"
    trans = "trans"


class RoomStatus(str, Enum):
    online = "online"
    offline = "offline"
    private = "private"
    away = "away"


class RoomListItem(BaseModel):
    """Una fila del listado público de salas online."""
    username: str = Field(..., description="username/handle público de la modelo")
    room_slug: str = Field(..., description="slug de la URL de la sala")
    display_name: str | None = None
    gender: Gender | None = None
    age: int | None = Field(None, ge=18, le=99)
    country: str | None = Field(None, description="ISO-2 o nombre del país mostrado")
    viewers: int = Field(0, ge=0)
    room_status: RoomStatus = RoomStatus.online
    tags: list[str] = Field(default_factory=list)
    thumbnail_url: HttpUrl | None = None
    is_hd: bool = False
    scraped_at: datetime = Field(default_factory=_utcnow)


class TipEvent(BaseModel):
    """Evento de tip individual extraído del chat público de la sala."""
    tipper_username: str
    amount: int = Field(..., ge=1)
    currency: str = Field("tokens", description="tokens, USD, etc.")
    message: str | None = None
    occurred_at: datetime = Field(default_factory=_utcnow)
    room_slug: str
    # Hash del username para no guardar PII cruda si se requiere (ver algoritmo)
    tipper_hash: str | None = None

    @field_validator("tipper_username")
    @classmethod
    def _norm_tipper(cls, v: str) -> str:
        return v.strip().lower()


class RoomDetail(BaseModel):
    """Detalle extendido de una sala (mezcla listing + snapshot en vivo)."""
    room_slug: str
    username: str
    display_name: str | None = None
    viewers: int = 0
    followers: int | None = None
    room_status: RoomStatus = RoomStatus.online
    # Métricas de la sesión actual
    session_started_at: datetime | None = None
    session_tokens: int = Field(0, ge=0, description="tokens acumulados en la sesión")
    top_tipper_session: str | None = None
    # Últimos N tips visibles en el chat público
    recent_tips: list[TipEvent] = Field(default_factory=list)
    scraped_at: datetime = Field(default_factory=_utcnow)


class ProfileDetail(BaseModel):
    """Perfil histórico/estadístico de una modelo."""
    username: str
    room_slug: str
    display_name: str | None = None
    bio: str | None = None
    gender: Gender | None = None
    age: int | None = None
    country: str | None = None
    followers: int = 0
    total_views: int | None = None
    # Horario declarado (string libre, lo normaliza el schedule matcher)
    declared_schedule: str | None = None
    avatar_url: HttpUrl | None = None
    tags: list[str] = Field(default_factory=list)
    scraped_at: datetime = Field(default_factory=_utcnow)


class ScheduleObservation(BaseModel):
    """Observación de conexión real (cuando vimos a la modelo online)."""
    room_slug: str
    observed_at: datetime
    was_online: bool
    viewers: int = 0
    day_of_week: int = Field(..., ge=0, le=6, description="0=Lunes .. 6=Domingo")
    hour: int = Field(..., ge=0, le=23, description="hora UTC")
