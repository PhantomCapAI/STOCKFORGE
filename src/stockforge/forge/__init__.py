"""Concept forge: turn a scored Signal into a launch-ready Concept."""

from .antislop import AntiSlop
from .concept import ConceptForge

__all__ = ["ConceptForge", "AntiSlop"]
