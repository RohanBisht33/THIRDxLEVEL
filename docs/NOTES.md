# AgentForge — Research Notes

> These notes were compiled from official Fetch.ai / ASI documentation.
> **Do not write card, payment, or agent code until you have read the relevant section here.**

---

## 1. ASI:One Interactive Cards

**Source:** https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards

### How Cards Work

A card-bearing chat message has two parts:
1. **TextContent block(s)** — plain narration shown in the chat bubble above the card.
2. **One MetadataContent block** — the card declaration (kind + JSON-stringified payload).

### Required Metadata Keys (dict[str, str] — all values are strings!)

| Key                        | Value                                                      |
|----------------------------|------------------------------------------------------------|
| `card_protocol_version`    | `"1"`                                                      |
| `requires_card_interaction`| `"true"`                                                   |
| `card_kind`                | `"carousel"` \| `"detail"` \| `"form"` \| `"review"` \| `"custom"` |
| `card_payload`             | JSON-stringified payload (shape depends on `card_kind`)    |

### Optional Metadata Keys

| Key                         | Value                       | Effect                                       |
|-----------------------------|-----------------------------|----------------------------------------------|
| `is_terminal`               | `"true"`                    | Informational only — no user input expected  |
| `preferred_drawer_width_px` | integer string, e.g. `"540"`| Hint for drawer width. Clamped to [320, 800] |

### Validation Limits

- `card_payload` must be **≤ 64 KB**.
- Custom element-tree nesting depth capped at **8 levels** (root = level 1).
- Unknown `card_kind` values are rejected.
- Payloads not matching the declared `card_kind` schema are rejected.
- On validation failure, ASI:One **silently falls back to plain text** (the TextContent still shows).

### Card Kinds — Payload Schemas

#### `carousel` — selectable items list
```json
{
  "title": "Choose a flight",
  "subtitle": "London → Edinburgh, 2026-06-25",
  "items": [
    {
      "id": "off_123",
      "image": "https://example.com/ba.png",
      "title": "British Airways",
      "subtitle": "Direct · 1h 15m · Economy",
      "badges": [{"label": "Direct", "variant": "info"}],
      "secondary_text": "USD 88.16",
      "primary_cta": {
        "label": "Select",
        "selection": {"offer_id": "off_123"}
      }
    }
  ]
}
```
**Selection shape:** whatever is under `primary_cta.selection`.

#### `detail` — detail view with sub-options + CTAs
```json
{
  "title": "British Airways · LHR → EDI",
  "hero_image": "https://example.com/hero.png",
  "summary_rows": [
    {"label": "Departure", "value": "21:50 · LHR"},
    {"label": "Arrival",   "value": "23:05 · EDI"}
  ],
  "sub_options": {
    "name": "cabin",
    "label": "Select cabin class",
    "options": [
      {"label": "Economy",      "value": "economy",      "secondary_text": "USD 88"},
      {"label": "Business",     "value": "business",     "secondary_text": "USD 310"}
    ]
  },
  "ctas": [
    {
      "label": "Book this flight",
      "selection": {"action": "book_offer", "offer_id": "off_123"}
    }
  ]
}
```
**Selection shape:** `selection` dict merged with chosen sub-option: `{"action": "book_offer", "offer_id": "off_123", "cabin": "economy"}`.

#### `form` — input form with fields
```json
{
  "title": "Passenger details",
  "fields": [
    {"name": "first_name",  "label": "First name",  "type": "text",   "required": true},
    {"name": "last_name",   "label": "Last name",   "type": "text",   "required": true},
    {"name": "email",       "label": "Email",        "type": "email",  "required": true},
    {"name": "notes",       "label": "Notes",        "type": "textarea"}
  ],
  "submit_label": "Continue"
}
```
**Selection shape:** `{"first_name": "...", "last_name": "...", "email": "...", "notes": "..."}`.

#### `review` — confirmation screen
```json
{
  "title": "Booking summary",
  "sections": [
    {
      "title": "Flight",
      "rows": [
        {"label": "Route",     "value": "LHR → EDI"},
        {"label": "Cabin",     "value": "Economy"},
        {"label": "Price",     "value": "USD 88.16"}
      ]
    },
    {
      "title": "Passenger",
      "rows": [
        {"label": "Name",  "value": "Jane Doe"},
        {"label": "Email", "value": "jane@example.com"}
      ]
    }
  ],
  "ctas": [
    {"label": "Confirm booking", "selection": {"action": "confirm", "booking_id": "bk_001"}},
    {"label": "Cancel",          "selection": {"action": "cancel"}}
  ]
}
```

#### `custom` — element tree primitives
Compose from: `section`, `list`, `image`, `heading`, `text`, `badge`, `button`.
Each list item can have `children` (rendered elements) and an `action.selection`.

### How Responses Come Back

When user interacts with a card, ASI:One sends a **follow-up ChatMessage** with the selection. Two formats:
1. **Natural-language prose (planner-mediated)** — e.g. "The user picked offer off_123 with cabin economy."
2. **Selection JSON (direct @mention)** — the `selection` dict serialized to a string.

**Best practice:** try `json.loads()` first, fall back to NLP parsing.

### Building a Card Message (Python)

```python
import json
from datetime import datetime, timezone
from uuid import uuid4
from uagents_core.contrib.protocols.chat import (
    ChatMessage, ChatAcknowledgement, MetadataContent, TextContent, chat_protocol_spec,
)

def card_message(narration: str, card_kind: str, card_payload: dict) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[
            TextContent(type="text", text=narration),
            MetadataContent(
                type="metadata",
                metadata={
                    "card_protocol_version": "1",
                    "requires_card_interaction": "true",
                    "card_kind": card_kind,
                    "card_payload": json.dumps(card_payload),
                    "preferred_drawer_width_px": "540",
                },
            ),
        ],
    )
```

### Common Pitfalls
- Forgetting `card_protocol_version: "1"` — card is silently dropped.
- Wrapping `card_payload` in an extra envelope `{"data": {...}}` — schema expects top-level.
- Setting `is_terminal: "true"` on a card needing user input — drawer never opens.
- Putting non-string values in `MetadataContent.metadata` — wire type is `dict[str, str]`.

---

## 2. Agentverse: Hosted Agents, Allowed Imports, Agent Storage, Chat Protocol

### Hosted Agent Runtime
- Hosted agents run in a managed cloud Python runtime.
- **Single agent per project** — no `Bureau` class on Agentverse.
- Do NOT include `if __name__ == '__main__': agent.run()` in hosted scripts.

### Allowed Imports (Hosted)
- Full Python standard library (json, math, datetime, asyncio, uuid, hashlib, etc.)
- `uagents` (core framework)
- `uagents_core` (protocols, chat, payment)
- `requests` (HTTP calls)
- `openai` (LLM APIs)
- `pydantic`
- **Not allowed:** arbitrary pip packages, C extensions, multi-file imports

### Workaround for Restricted Packages
Run locally with **Agentverse Mailbox** or **External Integration** to use full pip.

### Agent Storage (`ctx.storage`)
```python
ctx.storage.set("key_name", value)       # persist
val = ctx.storage.get("key_name")        # retrieve (returns None if missing)
```
- Supports JSON-compatible objects and Pydantic models.
- Persists across handler invocations.
- Viewable/editable via Agentverse Storage Viewer GUI.

### Agent Chat Protocol (ACP)
```python
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    StartSessionContent,
    EndSessionContent,
    chat_protocol_spec,
)

chat_proto = Protocol(spec=chat_protocol_spec)

@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    # 1. Always acknowledge
    await ctx.send(sender, ChatAcknowledgement(session=msg.session))
    # 2. Extract text
    text = msg.text()
    # 3. Process and respond...

agent.include(chat_proto, publish_manifest=True)
```

### Key ACP Notes
- Always send `ChatAcknowledgement` for every incoming `ChatMessage`.
- Include protocol with `publish_manifest=True` for discoverability.
- `MetadataContent` is imported from same chat module for interactive cards.

---

## 3. Payment Protocol (uagents_core.contrib.protocols.payment)

### Verified Model Schemas (uagents_core >= 0.4.x)

```python
from uagents_core.contrib.protocols.payment import (
    Funds,
    RequestPayment,
    CommitPayment,
    RejectPayment,
    CompletePayment,
    CancelPayment,
    payment_protocol_spec,
)
```

- **`Funds`**:
  - `amount`: float or int
  - `currency`: str (e.g. `"USD"`, `"FET"`)
  - `payment_method`: Optional[str] (e.g. `"crypto"`, `"stripe"`)

- **`RequestPayment`** (Seller -> Buyer, Invoice):
  - `accepted_funds`: List[Funds] (e.g. `[Funds(amount=7.00, currency="USD")]`)
  - `recipient`: str (Seller's agent address)
  - `deadline_seconds`: int (e.g. 300)
  - `reference`: Optional[str] (Order ID, e.g. `order.order_id`)
  - `description`: Optional[str]
  - `metadata`: Optional[dict]

- **`CommitPayment`** (Buyer -> Seller):
  - `funds`: Funds
  - `recipient`: str
  - `transaction_id`: str (Tx hash or Stripe test payment intent id)
  - `reference`: Optional[str]
  - `description`: Optional[str]
  - `metadata`: Optional[dict]

- **`CompletePayment`** (Seller -> Buyer, Receipt):
  - `transaction_id`: str

- **`RejectPayment`** (Buyer -> Seller):
  - `reason`: Optional[str]

- **`CancelPayment`** (Seller -> Buyer):
  - `transaction_id`: str
  - `reason`: Optional[str]

### Seller Role Implementation
```python
seller_payment_protocol = Protocol(name="AgentPaymentProtocol", version="0.1.0")

# Send invoice
async def request_payment(ctx, buyer_address, amount, currency, description):
    await ctx.send(buyer_address, RequestPayment(
        funds=Funds(amount=amount, currency=currency),
        description=description,
    ))

# Handle buyer commit
@seller_payment_protocol.on_message(model=CommitPayment, replies={CompletePayment, CancelPayment})
async def handle_commit(ctx, sender, msg):
    # Verify transaction...
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))

# Handle buyer reject
@seller_payment_protocol.on_message(model=RejectPayment, replies=set())
async def handle_reject(ctx, sender, msg):
    ctx.logger.info(f"Payment rejected by {sender}")
```

### Funds Model
```python
Funds(amount=100, currency="FET")   # or "USD", etc.
```

---

## 4. ASI1 Model API

### Endpoint
- **Base URL:** `https://api.asi1.ai/v1`
- **Chat Completion:** `POST /v1/chat/completions`
- **Auth:** `Authorization: Bearer <ASI1_API_KEY>`

### Available Models
| Model      | Context Window | Notes                    |
|------------|---------------|--------------------------|
| `asi1`     | 409,600       | General-purpose default  |
| `asi1-mini`| 262,144       | Fastest, lightest        |
| `asi1-ultra`| 1,048,576    | Most capable, deep reasoning |

### Request Format (OpenAI-compatible)
```python
import requests, json

response = requests.post(
    "https://api.asi1.ai/v1/chat/completions",
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ASI1_API_KEY}",
    },
    json={
        "model": "asi1-mini",
        "messages": [{"role": "user", "content": "..."}],
        "temperature": 0.7,
        "stream": False,
    },
    timeout=30,
)
result = response.json()
content = result["choices"][0]["message"]["content"]
```

### Response Structure
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "asi1",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "...",
      "reasoning_content": "..."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 231,
    "completion_tokens": 335,
    "total_tokens": 566
  }
}
```

---

## 5. Bundling Constraints for Agentverse

- Hosted agents are **single-file** — no multi-file imports.
- `scripts/bundle_for_agentverse.py` must inline `core/` into each agent file.
- Safe imports: `json`, `uuid`, `datetime`, `hashlib`, `math`, `re`, `copy`, `dataclasses`, `enum`, `typing`, `requests`, `pydantic`, `uagents`, `uagents_core`.
- **Avoid:** `os.path`, file I/O, subprocess, threading, multiprocessing, sqlite3, socket.
- The bundled agent should NOT contain `if __name__ == '__main__'` or `agent.run()`.
