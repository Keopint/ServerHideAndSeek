"""Правила доступности способностей для игровых сторон."""
from database.models import AbilityType, VictoryConditionType

# Охотники не могут укрываться, прячущиеся не могут ставить ловушки.
ROLE_FORBIDDEN_ABILITIES: dict[VictoryConditionType, set[AbilityType]] = {
    VictoryConditionType.SEEKER: {AbilityType.SAFE_HOUSE, AbilityType.SAFE_MANSION},
    VictoryConditionType.HIDER: {AbilityType.TRAP, AbilityType.SNARE},
}


def forbidden_abilities_for(victory_condition: VictoryConditionType) -> set[AbilityType]:
    return ROLE_FORBIDDEN_ABILITIES.get(victory_condition, set())
