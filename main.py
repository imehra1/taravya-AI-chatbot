from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import requests
import random
import re

# ── Groq Client ─────────────────────────────────────────────────────────────
client = Groq(
    api_key="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"
)

STORE_URL = "https://taravya.in"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schema ───────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str


# ── Product Fetcher ───────────────────────────────────────────────────────────
def get_products():
    response = requests.get(f"{STORE_URL}/products.json?limit=50", timeout=10)
    data = response.json()
    products = []

    for p in data.get("products", []):
        title = p.get("title", "")
        handle = p.get("handle", "")
        images = p.get("images", [])
        variants = p.get("variants", [])
        tags = p.get("tags", [])
        product_type = p.get("product_type", "")
        body_html = p.get("body_html", "")
        # Strip HTML tags from description
        description = re.sub(r"<[^>]+>", " ", body_html).strip()

        products.append({
            "title": title,
            "handle": handle,
            "url": f"https://taravya.in/products/{handle}",
            "image": images[0]["src"] if images else "",
            "price": variants[0]["price"] if variants else "0",
            "description": description[:300],
            "tags": tags,
            "product_type": product_type,
        })

    return products


# ── Price Extractor ───────────────────────────────────────────────────────────
def extract_budget(query: str):
    """
    Detects budget constraints from natural language.
    Returns (min_price, max_price) or (None, None) if no budget mentioned.

    Handles patterns like:
      "under 2000", "below 2k", "less than 1500",
      "under 2,000", "budget of 3000", "around 1500",
      "between 1000 and 3000", "above 2000", "more than 1k"
    """
    q = query.lower().replace(",", "")

    def parse_amount(s: str) -> float:
        s = s.strip()
        # Handle shorthand: 2k → 2000
        m = re.match(r"(\d+(?:\.\d+)?)\s*k$", s)
        if m:
            return float(m.group(1)) * 1000
        return float(s)

    # "between X and Y"
    m = re.search(r"between\s+(\d[\d.]*k?)\s+(?:and|to|-)\s+(\d[\d.]*k?)", q)
    if m:
        return parse_amount(m.group(1)), parse_amount(m.group(2))

    # "under / below / less than / within / upto / up to X"
    m = re.search(
        r"(?:under|below|less\s+than|within|upto|up\s+to|max(?:imum)?|budget\s+(?:of|is)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        return None, parse_amount(m.group(1))

    # "above / more than / over / minimum X"
    m = re.search(
        r"(?:above|more\s+than|over|min(?:imum)?|starting\s+(?:from|at)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        return parse_amount(m.group(1)), None

    # "around / approximately X"  →  ±20% band
    m = re.search(
        r"(?:around|approximately|about|near(?:ly)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        centre = parse_amount(m.group(1))
        return centre * 0.8, centre * 1.2

    return None, None


# ── Smart Product Matcher ─────────────────────────────────────────────────────
def match_products(user_message: str, products: list):
    """
    Returns (matched_products, budget_info_string).
    budget_info_string is non-empty when a budget was detected,
    so we can inject it into the AI prompt.
    """
    query = user_message.lower()

    # ── 1. Extract budget ────────────────────────────────────────────────────
    min_price, max_price = extract_budget(query)
    budget_info = ""
    if max_price is not None and min_price is None:
        budget_info = f"The customer's budget is under ₹{int(max_price):,}. Only recommend products priced at or below ₹{int(max_price):,}."
    elif min_price is not None and max_price is None:
        budget_info = f"The customer wants pieces above ₹{int(min_price):,}. Only recommend products priced at or above ₹{int(min_price):,}."
    elif min_price is not None and max_price is not None:
        budget_info = f"The customer's budget is between ₹{int(min_price):,} and ₹{int(max_price):,}. Only recommend products within this price range."

    # ── 2. Filter by price first ─────────────────────────────────────────────
    def in_budget(p):
        try:
            price = float(p["price"])
        except (ValueError, TypeError):
            return True  # keep if price unparseable
        if min_price is not None and price < min_price:
            return False
        if max_price is not None and price > max_price:
            return False
        return True

    pool = [p for p in products if in_budget(p)]

    # If budget filter wiped everything out, fall back gracefully
    if not pool:
        pool = products
        budget_info += " (Note: no products currently match this exact budget — showing closest alternatives.)"

    # ── 3. Score by keywords ─────────────────────────────────────────────────
    stopwords = {
        "i", "me", "my", "want", "need", "looking", "for", "a", "an", "the",
        "some", "any", "please", "can", "you", "show", "suggest", "recommend",
        "something", "is", "are", "do", "have", "get", "find", "what", "which",
        "best", "good", "nice", "beautiful", "pretty", "love", "like",
        "under", "below", "above", "less", "more", "than", "budget", "price",
        "cheap", "expensive", "affordable", "within", "around", "between",
        "2000", "1000", "3000", "1500", "500", "2k", "1k", "3k",
    }
    keywords = [
        w for w in re.findall(r"\w+", query)
        if w not in stopwords and len(w) > 2
    ]

    scored = []
    for product in pool:
        text = (
            product["title"] + " " +
            product["description"] + " " +
            product["product_type"] + " " +
            " ".join(product["tags"] if isinstance(product["tags"], list) else [])
        ).lower()

        score = sum(
            2 if kw in product["title"].lower() else 1
            for kw in keywords if kw in text
        )
        scored.append((score, product))

    scored.sort(key=lambda x: x[0], reverse=True)

    # If top results all have score 0 (generic query), sort pool by price asc
    if scored and scored[0][0] == 0 and max_price:
        scored.sort(key=lambda x: float(x[1]["price"] or 0))

    matched = [p for _, p in scored[:3]]

    # Final fallback
    if not matched:
        matched = random.sample(pool, min(3, len(pool)))

    return matched, budget_info


# ── System Prompt Builder ─────────────────────────────────────────────────────
def build_system_prompt(product_context: str, budget_info: str = "") -> str:
    budget_block = f"\nBUDGET CONSTRAINT (CRITICAL — never violate this):\n{budget_info}\n" if budget_info else ""
    return f"""You are the AI concierge for *Taravya*, an exquisite sterling silver jewellery brand from India.

Your persona: refined, warm, knowledgeable — like a trusted personal jeweller who has dressed discerning women for generations. You speak with quiet authority and genuine passion for jewellery craft.

Your role is to:
- Understand what the customer truly desires (occasion, mood, recipient, aesthetic)
- Recommend pieces from the curated selection below with genuine enthusiasm
- Explain *why* a piece suits them — its character, craftsmanship, when to wear it
- Use evocative, sensory language that makes the jewellery come alive
- Be concise but never cold — warm elegance is your register
{budget_block}
AVAILABLE PIECES TODAY (with prices):
{product_context}

STRICT RULES:
- Only mention products from the list above — never invent names or prices
- NEVER recommend or mention any product whose price exceeds the customer's stated budget
- Keep your response to 3–5 sentences maximum
- Do NOT mention URLs, links, or image paths
- Do NOT use bullet points or numbered lists
- Speak in flowing prose, as a luxury stylist would
- Use markdown: **bold** for product names, *italics* for evocative descriptors
- End with a gentle question or invitation to refine their choice
- If the query is off-topic (not about jewellery or styling), gracefully redirect

Remember: every word you write reflects the Taravya brand — understated luxury, timeless craft."""


# ── Chat Endpoint ─────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        products = get_products()
        matched_products, budget_info = match_products(req.message, products)

        # Build product context for the prompt — include price explicitly
        product_context = "\n".join([
            f"- **{p['title']}** — ₹{float(p['price']):,.0f}"
            + (f" | {p['description'][:120]}" if p["description"] else "")
            for p in matched_products
        ])

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(product_context, budget_info),
                },
                {
                    "role": "user",
                    "content": req.message,
                },
            ],
            temperature=0.75,
            max_tokens=280,
        )

        ai_reply = completion.choices[0].message.content.strip()

        return {
            "reply": ai_reply,
            "products": matched_products,
        }

    except requests.exceptions.RequestException as e:
        return {
            "reply": "I'm having trouble connecting to our collection right now. Please try again in a moment.",
            "products": [],
        }
    except Exception as e:
        return {
            "reply": f"An unexpected error occurred: {str(e)}",
            "products": [],
        }