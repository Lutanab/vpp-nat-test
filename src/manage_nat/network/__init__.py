"""Сетевые операции для CLI `manage-nat`."""

from .clean import clean_network
from .setup import setup_network

__all__ = ["setup_network", "clean_network"]
