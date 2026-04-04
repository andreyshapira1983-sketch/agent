# core.bare_metal — автономный мозг на чистом PyTorch
# Своя архитектура, свой tokenizer, свой inference,
# свой memory manager, свой action loop.

from .config import BrainConfig
from .brain import BareMetalBrain

__all__ = ['BrainConfig', 'BareMetalBrain']
