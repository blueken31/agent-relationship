"""Optional, dependency-free observation adapters; hosts retain execution control."""

from .agents_api import AgentsAPIAdapter, HostBinding

__all__ = ["AgentsAPIAdapter", "HostBinding"]
