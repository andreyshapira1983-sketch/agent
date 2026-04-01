# social — Social Interaction Model (Слой 43)
# Модель участников, доверие, стиль общения, контекст разговора, эмоции.
from .social_model import (
    SocialInteractionModel, SocialActor, ConversationContext,
    RelationshipType, TrustLevel, CommunicationStyle,
)
from .emotional_state import EmotionalState, Mood

__all__ = [
    'SocialInteractionModel', 'SocialActor', 'ConversationContext',
    'RelationshipType', 'TrustLevel', 'CommunicationStyle',
    'EmotionalState', 'Mood',
]
