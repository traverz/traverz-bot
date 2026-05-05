"""Agent core module."""
from __future__ import annotations


def __getattr__(name: str):
    _MAP = {
        "ContextBuilder": ("nanobot.agent.context", "ContextBuilder"),
        "AgentHook": ("nanobot.agent.hook", "AgentHook"),
        "AgentHookContext": ("nanobot.agent.hook", "AgentHookContext"),
        "CompositeHook": ("nanobot.agent.hook", "CompositeHook"),
        "AgentLoop": ("nanobot.agent.loop", "AgentLoop"),
        "Dream": ("nanobot.agent.memory", "Dream"),
        "MemoryStore": ("nanobot.agent.memory", "MemoryStore"),
        "SkillsLoader": ("nanobot.agent.skills", "SkillsLoader"),
        "SubagentManager": ("nanobot.agent.subagent", "SubagentManager"),
    }
    if name in _MAP:
        import importlib
        mod_path, attr = _MAP[name]
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr)
    raise AttributeError(f"module 'nanobot.agent' has no attribute {name!r}")

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
