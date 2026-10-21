"""
Inter-agent message models for AgentForge.
Used for communication between Builder Agent and Extractor Agent.
"""

from typing import Any, Dict, List, Optional
from uagents import Model


class ExtractCatalogRequest(Model):
    """Request sent by Builder to Extractor."""
    session_id: str
    business_name: str
    raw_content: str
    format_hint: str = "auto"  # 'auto', 'text', 'csv'


class ExtractCatalogResponse(Model):
    """Response returned by Extractor to Builder."""
    session_id: str
    success: bool
    catalog_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    confidence_score: float = 1.0
