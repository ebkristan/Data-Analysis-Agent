# -*- coding: utf-8 -*-
"""Knowledge package exports with lazy imports.

Importing Function.Knowledge.neural_embedder should not initialize the full
KnowledgeBase stack, because that stack loads jieba for SQLite FTS support.
"""

__all__ = ["KnowledgeBase"]


def __getattr__(name):
    if name == "KnowledgeBase":
        from .knowledge_base import KnowledgeBase
        return KnowledgeBase
    raise AttributeError(name)