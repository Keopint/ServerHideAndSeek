from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Float,
    JSON, Enum, ForeignKey, Table
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid
from sqlalchemy.orm import validates

Base = declarative_base()

# Enums as Python enums
import enum


class GameStatus(enum.Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"

class EffectType(enum.Enum):
    SHIELD = "SHIELD"
    TRAPPED = "TRAPPED"
    ROOTED = "ROOTED"
    INTEL = "INTEL"
    SCAN = "SCAN"

class ZoneType(enum.Enum):
    SAFE = "SAFE"
    DANGER = "DANGER"
    WARNING = "WARNING"
    AIRDROP = "AIRDROP"
    TRAP = "TRAP"
    SNARE = "SNARE"
    SAFE_HOUSE = "SAFE_HOUSE"
    SAFE_MANSION = "SAFE_MANSION"

# SAFE - начальная зона
# DANGER - красная зона (бомба)
# WARNING - оранжевая зона (бомбардировка)
# AIRDROP - аирдроп
# SNARE - капкан
# TRAP - ловушка
# SAFE_HOUSE - Я в домике
# SAFE_MANSION - Я в особняке

class AbilityType(enum.Enum):
    SHIELD = "SHIELD"
    INTEL = "INTEL"
    SCAN = "SCAN"
    PERSONAL_BOMB = "PERSONAL_BOMB"
    TRAP = "TRAP"
    SNARE = "SNARE"
    SAFE_HOUSE = "SAFE_HOUSE"
    SAFE_MANSION = "SAFE_MANSION"

class ActivationFrequencyType(enum.Enum):
    FREQUENT = "FREQUENT"
    COMMON = "COMMON"
    RARE = "RARE"

class EventType(enum.Enum):
    BOMB = "BOMB"
    AIRDROP = "AIRDROP"
    BOMBARDMENT = "BOMBARDMENT"
    REVEAL = "REVEAL"

# BOMB - бомба (красная зона в рандомном месте)
# AIRDROP - аирдроп (желтая зона в рандомном месте)
# BOMBARDMENT - бомбардировка (много ораньжевых зон в разных местах карты)
# REVEAL - подсветка всех игроков (игроки видят геолокации друг-друга)

class VictoryConditionType(enum.Enum):
    SEEKER = "SEEKER"
    HIDER = "HIDER"


# # Association tables for many-to-many relationships
# role_victory_conditions = Table(
#     'role_victory_conditions',
#     Base.metadata,
#     Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
#     Column('role_id', UUID(as_uuid=True), ForeignKey('roles.id'), nullable=False),
#     Column('victory_conditions_id', UUID(as_uuid=True), ForeignKey('victory_conditions.id'), nullable=False)
# )

role_abilities = Table(
    'role_abilities',
    Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('role_id', UUID(as_uuid=True), ForeignKey('roles.id'), nullable=False),
    Column('ability_id', UUID(as_uuid=True), ForeignKey('abilities.id'), nullable=False)
)

game_roles = Table(
    'game_roles',
    Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('game_id', UUID(as_uuid=True), ForeignKey('games.id'), nullable=False),
    Column('role_id', UUID(as_uuid=True), ForeignKey('roles.id'), nullable=False)
)

role_events = Table(
    'role_events',
    Base.metadata,
    Column('id', UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column('role_id', UUID(as_uuid=True), ForeignKey('roles.id'), nullable=False),
    Column('event_id', UUID(as_uuid=True), ForeignKey('events.id'), nullable=False)
)


# ... (все импорты и классы до Role без изменений) ...

class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    health = Column(Integer, nullable=False, default=100)
    victory_condition = Column(Enum(VictoryConditionType), nullable=False, default=VictoryConditionType.HIDER)

    abilities = relationship(
        "Ability",
        secondary=role_abilities,
        back_populates="roles"
    )
    games = relationship(
        "Game",
        secondary=game_roles,
        back_populates="roles"
    )
    events = relationship(
        "Event",
        secondary=role_events,
        back_populates="roles"
    )


class Ability(Base):
    __tablename__ = "abilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ability_type = Column(Enum(AbilityType), nullable=False)
    recharge_time = Column(Integer, nullable=False, comment="время перезарядки в секундах")
    number_uses = Column(Integer, nullable=False, comment="количество использований")
    duration_seconds = Column(Integer, default=None)
    data = Column(JSON, comment="additional parameters", default=None)

    roles = relationship(
        "Role",
        secondary=role_abilities,
        back_populates="abilities"
    )
    players = relationship("PlayerAbility", back_populates="ability")
    used_by_players = relationship("UsedAbility", back_populates="ability_ref")

class GameZone(Base):
    __tablename__ = "game_zones"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    game_id = Column(UUID, ForeignKey("games.id"))
    type = Column(Enum(ZoneType))
    center_lat = Column(Float, nullable=False)
    center_lng = Column(Float, nullable=False)
    radius = Column(Float, nullable=False)
    damage = Column(Integer, default=0)
    starts_at = Column(DateTime(timezone=True))
    ends_at = Column(DateTime(timezone=True))
    created_by = Column(UUID, ForeignKey("players.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    zone_data = Column(JSON, nullable=False, default={})

    # Добавляем обратную связь к игре (опционально)
    game = relationship("Game", back_populates="game_zones", foreign_keys=[game_id])

class PlayerEffect(Base):
    __tablename__ = "player_effects"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID, ForeignKey("players.id"))
    type = Column(Enum(EffectType))
    starts_at = Column(DateTime(timezone=True))
    ends_at = Column(DateTime(timezone=True))
    data = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)

class GameEvent(Base):
    __tablename__ = "game_events"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    game_id = Column(UUID, ForeignKey("games.id"))
    event_type = Column(Enum(EventType))
    starts_at = Column(DateTime(timezone=True))
    ends_at = Column(DateTime(timezone=True), nullable=True)

    game = relationship("Game", back_populates="game_events")

class PlayerAbility(Base):
    __tablename__ = "player_abilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    ability_id = Column(UUID(as_uuid=True), ForeignKey("abilities.id"), nullable=False)
    number_uses_left = Column(Integer, nullable=False, comment="количество оставшихся использований")

    player = relationship("Player", back_populates="abilities")
    ability = relationship("Ability", back_populates="players")

class Game(Base):
    __tablename__ = "games"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_code = Column(String(6), nullable=False)
    name = Column(String, nullable=False)
    status = Column(Enum(GameStatus), nullable=False, default=GameStatus.WAITING)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow().replace(second=0, microsecond=0))
    safe_zone_center_lat = Column(Float, nullable=False)
    safe_zone_center_lng = Column(Float, nullable=False)
    safe_zone_radius = Column(Float, nullable=False, default=500.0)
    min_zone_radius = Column(Float, nullable=False, default=50.0)
    zone_shrink_interval = Column(Integer, nullable=False, default=120)
    game_duration = Column(Integer, nullable=False, default=1800)
    zone_boundary_damage = Column(Integer, nullable=False, default=1)
    current_safe_zone_id = Column(UUID(as_uuid=True), ForeignKey("game_zones.id"),
                                  comment="optional, references active safe zone")
    last_shrink_at = Column(DateTime, comment="last time when zone was active")

    players = relationship("Player", back_populates="game", cascade="all, delete-orphan")
    roles = relationship(
        "Role",
        secondary=game_roles,
        back_populates="games"
    )
    # Исправлено: вместо несуществующего Effect используем GameEvent и PlayerEffect (если нужно)
    # Но так как ранее был 'effects', я его убираю, так как класс Effect отсутствует.
    # При желании можно добавить relationship для GameEvent и GameZone.
    game_zones = relationship("GameZone", back_populates="game", foreign_keys="GameZone.game_id", cascade="all, delete-orphan")
    game_events = relationship("GameEvent", back_populates="game", cascade="all, delete-orphan")
    snapshots = relationship("GameStateSnapshot", back_populates="game", cascade="all, delete-orphan")

    # Исправлено: current_safe_zone ссылается на GameZone
    current_safe_zone = relationship("GameZone", foreign_keys=[current_safe_zone_id], post_update=True)

class Player(Base):
    __tablename__ = "players"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id = Column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
    name = Column(String, nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    health = Column(Integer, nullable=False, default=100)
    is_alive = Column(Boolean, nullable=False, default=True)
    location_lat = Column(Float, nullable=False)
    location_lng = Column(Float, nullable=False)
    last_location_update = Column(DateTime, nullable=False, default=lambda: datetime.utcnow().replace(second=0, microsecond=0))
    trapped_until = Column(DateTime, nullable=True)
    player_data = Column(JSON, comment="additional attributes like inventory")
    is_player_ready = Column(Boolean, nullable=False, default=False)

    game = relationship("Game", back_populates="players")
    abilities = relationship("PlayerAbility", back_populates="player", cascade="all, delete-orphan")
    used_abilities = relationship("UsedAbility", back_populates="player", foreign_keys="UsedAbility.player_id")
    targeted_in_abilities = relationship(
        "UsedAbility",
        back_populates="target_player",
        foreign_keys="UsedAbility.target_player_id"
    )
    role_ref = relationship("Role", foreign_keys=[role_id])  # указали внешний ключ

class UsedAbility(Base):
    __tablename__ = "used_abilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    ability = Column(Enum(AbilityType), nullable=False)
    ability_id = Column(UUID(as_uuid=True), ForeignKey("abilities.id"), nullable=True)
    used_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow().replace(second=0, microsecond=0))
    location_lat = Column(Float, nullable=False)
    location_lng = Column(Float, nullable=False)

    target_player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=True)
    result = Column(JSON, comment="details of the ability outcome")

    player = relationship("Player", back_populates="used_abilities", foreign_keys=[player_id])
    target_player = relationship("Player", back_populates="targeted_in_abilities", foreign_keys=[target_player_id])
    ability_ref = relationship("Ability", back_populates="used_by_players")

class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(Enum(EventType), nullable=False)
    activation_frequency = Column(Enum(ActivationFrequencyType), nullable=False)
    event_data = Column(JSON, nullable=False, default={})

    roles = relationship(
        "Role",
        secondary=role_events,
        back_populates="events"
    )

class GameStateSnapshot(Base):
    __tablename__ = "game_state_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id = Column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
    snapshot_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow().replace(second=0, microsecond=0))
    state = Column(JSON, nullable=False, comment="full game state dump")

    game = relationship("Game", back_populates="snapshots")

# class VictoryCondition(Base):
#     __tablename__ = "victory_conditions"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     data = Column(JSON, nullable=False, comment="victory_condition information")
#
#     # Relationships
#     roles = relationship(
#         "Role",
#         secondary=role_victory_conditions,
#         back_populates="victory_conditions"
#     )
#     players = relationship(
#         "PlayerVictoryCondition",
#         back_populates="victory_condition"
#     )


# class PlayerVictoryCondition(Base):
#     __tablename__ = "player_victory_conditions"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
#     victory_conditions_id = Column(UUID(as_uuid=True), ForeignKey("victory_conditions.id"), nullable=False)
#     role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=True)
#     is_done = Column(Boolean, nullable=False, default=False)
#
#     # Relationships
#     player = relationship("Player", back_populates="victory_conditions")
#     victory_condition = relationship("VictoryCondition", back_populates="players")
#     role = relationship("Role", back_populates="player_victory_conditions")


# class Effect(Base):
#     __tablename__ = "Effect"
#
#     id = Column(UUID, primary_key=True, default=uuid.uuid4)
#     game_id = Column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
#     type = Column(Enum(EffectType))
#     duration_seconds = Column(Integer, nullable=False)
#
#     # Relationships
#     game = relationship("Game", back_populates="effects")
#
#     @validates("duration_seconds")
#     def _validate_population(self, key, duration_seconds):
#         if not duration_seconds:
#             return duration_seconds
#         if duration_seconds < 0 or duration_seconds > 1000000:
#             raise ValueError(f"Недопустимое значение для duration_seconds: {duration_seconds}")
#         return duration_seconds



# class Zone(Base):
#     __tablename__ = "zones"
#
#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     game_id = Column(UUID(as_uuid=True), ForeignKey("games.id"), nullable=False)
#     type = Column(Enum(ZoneType), nullable=False)
#     radius = Column(Float, nullable=False)
#     duration_seconds = Column(Integer, nullable=False)
#     zone_data = Column(JSON, nullable=False, default={})
#
#     # Relationships
#     game = relationship("Game", back_populates="zones", foreign_keys=[game_id])
#
#     @validates("duration_seconds")
#     def _validate_population(self, key, duration_seconds):
#         if not duration_seconds:
#             return duration_seconds
#         if duration_seconds < 0 or duration_seconds > 1000000:
#             raise ValueError(f"Недопустимое значение для duration_seconds: {duration_seconds}")
#         return duration_seconds


