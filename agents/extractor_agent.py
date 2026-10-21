"""
Extractor Agent for AgentForge.
Extracts structured catalog items from menu text or CSV using ASI1 Model API or local parser.
Returns schema-validated JSON with confidence scores per field.
"""

from __future__ import annotations
import csv
from datetime import datetime, timezone
import io
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import requests
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from core.catalog import Business, Item
from .messages import ExtractCatalogRequest, ExtractCatalogResponse


EXTRACTION_SYSTEM_PROMPT = """You are an expert catalog parser. Extract business information and items from the provided text into strict, valid JSON.
Output ONLY a raw JSON object with NO markdown formatting, NO backticks, and NO extra commentary.

The JSON must follow this exact structure:
{
  "name": "Business Name",
  "hours": "Operating hours or 'Standard hours'",
  "policies": "Policies or 'Standard shop policies'",
  "items": [
    {
      "name": "Item Name",
      "price": 12.50,
      "currency": "USD",
      "options": ["Option 1", "Option 2"],
      "stock": 10,
      "available": true,
      "confidence": 0.95,
      "description": "Short description"
    }
  ]
}

Rules:
1. Prices must be non-negative numeric numbers (floats or integers).
2. Currency must be uppercase ISO code (e.g. 'USD', 'FET', 'EUR').
3. Stock must be an integer (e.g. 15), 0 for sold out, or null if unlimited.
4. If an item is sold out, set available: false or stock: 0.
5. Provide a realistic confidence score between 0.0 and 1.0 for each item.
"""


def parse_csv_catalog(content: str, business_name: str = "Shop") -> Dict[str, Any]:
    """Deterministically parse CSV content into schema-validated catalog dictionary."""
    f = io.StringIO(content.strip())
    reader = csv.DictReader(f)
    items: List[Dict[str, Any]] = []

    for row in reader:
        # Normalize keys to lowercase
        norm_row = {k.strip().lower() if k else "": v.strip() for k, v in row.items()}
        name = norm_row.get("name") or norm_row.get("item") or norm_row.get("title") or "Unknown Item"
        
        # Price extraction
        raw_price = norm_row.get("price") or norm_row.get("unit_price") or "0.00"
        raw_price_clean = re.sub(r"[^\d.]", "", raw_price)
        price = float(raw_price_clean) if raw_price_clean else 0.0

        currency = norm_row.get("currency") or "USD"
        
        # Stock extraction
        raw_stock = norm_row.get("stock") or norm_row.get("quantity") or norm_row.get("qty")
        stock = None
        if raw_stock:
            try:
                stock = int(re.sub(r"[^\d]", "", raw_stock))
            except ValueError:
                stock = None

        # Options
        raw_opts = norm_row.get("options") or norm_row.get("variants") or ""
        options = [o.strip() for o in raw_opts.split(",") if o.strip()]

        desc = norm_row.get("description") or norm_row.get("desc") or ""
        available = True
        if stock is not None and stock <= 0:
            available = False
        if "out of stock" in desc.lower():
            available = False

        items.append({
            "name": name,
            "price": price,
            "currency": currency.upper(),
            "options": options,
            "stock": stock,
            "available": available,
            "confidence": 1.0,
            "description": desc,
        })

    biz_id = re.sub(r"[^a-zA-Z0-9_]", "", business_name.lower().replace(" ", "_")).strip() or "shop"
    return {
        "id": biz_id,
        "name": business_name,
        "hours": "9:00 AM - 6:00 PM",
        "policies": "Standard store policies apply.",
        "items": items,
    }


def parse_text_catalog_heuristic(content: str, business_name: str = "Artisan Shop") -> Dict[str, Any]:
    """Heuristic fallback parser for menu text when LLM key is absent or unreachable."""
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    items: List[Dict[str, Any]] = []
    hours = "Standard Hours"
    policies = "Standard shop policies apply."

    # Look for business name at the top if not specified
    if lines and (not business_name or business_name == "Shop" or business_name == "Artisan Shop"):
        first_line = lines[0]
        if ":" not in first_line and "$" not in first_line:
            business_name = first_line

    for line in lines:
        lower = line.lower()
        if lower.startswith("hours:"):
            hours = line.split(":", 1)[1].strip()
            continue
        if lower.startswith("policies:") or lower.startswith("policy:"):
            policies = line.split(":", 1)[1].strip()
            continue

        # Item pattern: Name: $Price (Notes)
        # e.g., "Sourdough Loaf: $6.50 (Naturally leavened... In stock: 15)"
        price_match = re.search(r"\$([0-9]+\.?[0-9]*)", line)
        if price_match:
            price = float(price_match.group(1))
            name_part = line.split(":")[0].replace("-", "").replace("•", "").strip()
            
            # Stock extraction
            stock = None
            stock_match = re.search(r"(?:in stock|stock):\s*([0-9]+)", lower)
            if stock_match:
                stock = int(stock_match.group(1))
            elif "sold out" in lower or "stock: 0" in lower:
                stock = 0

            available = True
            if stock is not None and stock <= 0:
                available = False

            # Description in parentheses
            desc = ""
            desc_match = re.search(r"\((.*?)\)", line)
            if desc_match:
                desc = desc_match.group(1).strip()

            confidence = 0.95
            items.append({
                "name": name_part,
                "price": price,
                "currency": "USD",
                "options": [],
                "stock": stock,
                "available": available,
                "confidence": confidence,
                "description": desc,
            })

    biz_id = re.sub(r"[^a-zA-Z0-9_]", "", business_name.lower().replace(" ", "_")).strip() or "shop"
    return {
        "id": biz_id,
        "name": business_name,
        "hours": hours,
        "policies": policies,
        "items": items,
    }


def call_asi1_model_api(
    raw_content: str,
    business_name: str,
    api_key: str,
    max_retries: int = 2,
    timeout: int = 20,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Call ASI1 OpenAI-compatible model API with retry loop on malformed JSON.
    
    Retries up to max_retries (total 3 attempts).
    Returns (success, catalog_dict, error_message).
    """
    model_name = os.getenv("ASI1_MODEL", "asi1-mini")
    url = "https://api.asi1.ai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    user_prompt = (
        f"Business Name: {business_name}\n"
        f"Please parse the following catalog content:\n\n"
        f"{raw_content}"
    )

    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code != 200:
                last_error = f"ASI1 API HTTP {resp.status_code}: {resp.text}"
                time.sleep(1)
                continue

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Clean any accidental markdown backticks
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)

            parsed = json.loads(content)
            # Validate with Business schema
            biz = Business.from_dict(parsed)
            return True, biz.to_dict(), None

        except (json.JSONDecodeError, KeyError, ValueError) as err:
            last_error = f"Malformed catalog JSON (attempt {attempt + 1}/{max_retries + 1}): {str(err)}"
            time.sleep(1)
        except requests.RequestException as req_err:
            last_error = f"Network error contacting ASI1 API: {str(req_err)}"
            time.sleep(1)

    return False, None, last_error


def extract_catalog(
    raw_content: str,
    business_name: str = "My Shop",
    format_hint: str = "auto",
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """Primary extraction dispatcher.
    
    Tries CSV parser, ASI1 API (if key present), or heuristic parser.
    Always produces schema-validated catalog data.
    """
    content = raw_content.strip()
    if not content:
        return False, {}, "Empty menu content provided."

    # 1. Check if input is CSV format
    is_csv = format_hint == "csv" or (
        "\n" in content and "," in content.split("\n")[0] and any(k in content.lower() for k in ["price", "item", "stock"])
    )
    if is_csv:
        try:
            data = parse_csv_catalog(content, business_name)
            biz = Business.from_dict(data)
            return True, biz.to_dict(), None
        except Exception as e:
            return False, {}, f"Failed to parse CSV: {str(e)}"

    # 2. Try ASI1 API if API key is configured
    api_key = os.getenv("ASI1_API_KEY", "").strip()
    if api_key and api_key != "your_asi1_api_key_here":
        success, data, err = call_asi1_model_api(content, business_name, api_key)
        if success and data:
            return True, data, None

    # 3. Fallback to heuristic parser
    try:
        data = parse_text_catalog_heuristic(content, business_name)
        biz = Business.from_dict(data)
        if not biz.items:
            return False, {}, "Could not identify any valid items or prices in the provided text."
        return True, biz.to_dict(), None
    except Exception as e:
        return False, {}, f"Heuristic extraction error: {str(e)}"


def create_extractor_agent(
    name: str = "extractor_agent",
    seed: Optional[str] = None,
    port: Optional[int] = 8002,
) -> Agent:
    """Instantiate the Extractor Agent."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    seed_phrase = seed or os.getenv("EXTRACTOR_AGENT_SEED", "agentforge_extractor_seed_demo_2026")
    kwargs = {"name": name, "seed": seed_phrase, "loop": loop}
    if port is not None:
        kwargs["port"] = port
        kwargs["endpoint"] = [f"http://127.0.0.1:{port}/submit"]

    agent = Agent(**kwargs)

    # 1. Custom inter-agent protocol for Builder requests
    extractor_proto = Protocol(name="CatalogExtractorProtocol", version="0.1.0")

    @extractor_proto.on_message(model=ExtractCatalogRequest, replies={ExtractCatalogResponse})
    async def handle_extract_request(ctx: Context, sender: str, msg: ExtractCatalogRequest):
        ctx.logger.info(f"Extractor received request for '{msg.business_name}' from {sender}")
        success, catalog_dict, error = extract_catalog(
            raw_content=msg.raw_content,
            business_name=msg.business_name,
            format_hint=msg.format_hint,
        )
        response = ExtractCatalogResponse(
            session_id=msg.session_id,
            success=success,
            catalog_data=catalog_dict if success else None,
            error_message=error,
            confidence_score=0.95 if success else 0.0,
        )
        await ctx.send(sender, response)

    agent.include(extractor_proto)

    # 2. Agent Chat Protocol (ACP) for direct user chat & Agentverse discovery
    chat_proto = Protocol(spec=chat_protocol_spec)

    @chat_proto.on_message(ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        text = msg.text()
        success, catalog_dict, error = extract_catalog(text)
        if success:
            reply_text = f"Successfully extracted catalog for {catalog_dict.get('name')}:\n" + json.dumps(catalog_dict, indent=2)
        else:
            reply_text = f"Extraction failed: {error}"
        await ctx.send(sender, ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[TextContent(type="text", text=reply_text)],
        ))

    @chat_proto.on_message(ChatAcknowledgement)
    async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    agent.include(chat_proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    extractor = create_extractor_agent()
    extractor.run()
