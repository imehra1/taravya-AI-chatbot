from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import requests
import random
import re

# ── Groq Client ─────────────────────────────────────────────────────────────

client = Groq(
    api_key="YOUR_GROQ_API_KEY"
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

# ── Schema ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str

# ── Product Fetcher ─────────────────────────────────────────────────────────

def get_products():

    response = requests.get(
        f"{STORE_URL}/products.json?limit=50",
        timeout=10
    )

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

        description = re.sub(
            r"<[^>]+>",
            " ",
            body_html
        ).strip()

        products.append({

            "title":
            title,

            "handle":
            handle,

            "url":
            f"https://taravya.in/products/{handle}",

            "image":
            images[0]["src"]
            if images else "",

            "price":
            variants[0]["price"]
            if variants else "0",

            "description":
            description[:300],

            "tags":
            tags,

            "product_type":
            product_type,

        })

    return products

# ── Budget Extractor ────────────────────────────────────────────────────────

def extract_budget(query: str):

    q = query.lower().replace(",", "")

    m = re.search(
        r"(?:under|below|less than|within|upto|up to)\s*(?:₹|rs\.?)?\s*(\d+)",
        q
    )

    if m:
        return None, float(m.group(1))

    m = re.search(
        r"(?:above|over|more than)\s*(?:₹|rs\.?)?\s*(\d+)",
        q
    )

    if m:
        return float(m.group(1)), None

    return None, None

# ── Synonyms ────────────────────────────────────────────────────────────────

SYNONYMS = {

    "rings": ["ring", "band"],
    "ring": ["ring", "band"],

    "earrings": ["earring", "stud", "hoop"],
    "earring": ["earring", "stud", "hoop"],

    "necklace": ["necklace", "chain", "pendant"],
    "necklaces": ["necklace", "chain", "pendant"],

    "bracelet": ["bracelet", "bangle"],
    "bracelets": ["bracelet", "bangle"],

    "gift": ["gift", "present", "set"],

    "minimal": ["minimal", "simple", "daily"],
    "daily": ["daily", "minimal", "casual"],

    "bridal": ["bridal", "wedding"],
    "wedding": ["bridal", "wedding"],

}

# ── Matcher ─────────────────────────────────────────────────────────────────

def match_products(user_message: str, products: list):

    query = user_message.lower()

    min_price, max_price = extract_budget(query)

    def within_budget(product):

        try:
            price = float(product["price"])
        except:
            return True

        if min_price and price < min_price:
            return False

        if max_price and price > max_price:
            return False

        return True

    filtered = [
        p for p in products
        if within_budget(p)
    ]

    if not filtered:
        filtered = products

    keywords = re.findall(r"\w+", query)

    expanded_keywords = set()

    for kw in keywords:

        expanded_keywords.add(kw)

        if kw in SYNONYMS:
            expanded_keywords.update(
                SYNONYMS[kw]
            )

    scored = []

    for product in filtered:

        searchable = (

            product["title"] + " " +
            product["description"] + " " +
            product["product_type"]

        ).lower()

        score = 0

        for kw in expanded_keywords:

            if kw in searchable:
                score += 1

        scored.append((score, product))

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    matched = [
        p for score, p in scored
        if score > 0
    ][:3]

    if not matched:

        matched = random.sample(
            filtered,
            min(3, len(filtered))
        )

    return matched

# ── System Prompt ───────────────────────────────────────────────────────────

def build_system_prompt(
    product_context: str
):

    return f"""

You are the AI concierge for Taravya,
a luxury sterling silver jewellery brand.

Your personality:
- elegant
- warm
- sophisticated
- emotionally intelligent
- conversational
- never robotic
- never pushy

IMPORTANT:

If the user casually says:
"hi"
"hello"
"hey"

DO NOT immediately recommend products.

Instead:
- warmly welcome them
- continue conversation naturally
- ask what style or occasion they are shopping for

ONLY recommend products when:
- user asks for suggestions
- asks for gifts
- asks for jewellery
- asks for styling help
- asks for budgets
- asks what to buy

AVAILABLE PRODUCTS:

{product_context}

STRICT RULES:

- Mention ONLY products provided above
- Never invent jewellery names
- Never invent prices
- Never mention image URLs
- Never mention raw links
- Keep responses conversational
- Sound like a luxury stylist
- Keep responses concise
- Maximum 4 short paragraphs

EXAMPLE GOOD RESPONSE:

"Welcome to Taravya ✨
I’d be delighted to help you discover something beautiful today. Are you shopping for yourself or searching for a special gift?"
"""

# ── Chat Endpoint ───────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):

    try:

        products = get_products()

        matched_products = match_products(
            req.message,
            products
        )

        product_context = "\n".join([

            f"""
            Product:
            {p['title']}

            Price:
            Rs.{float(p['price']):,.0f}

            Description:
            {p['description'][:120]}
            """

            for p in matched_products
        ])

        completion = client.chat.completions.create(

            model="llama-3.3-70b-versatile",

            messages=[

                {
                    "role": "system",

                    "content":
                    build_system_prompt(
                        product_context
                    )
                },

                {
                    "role": "user",

                    "content":
                    req.message
                }

            ],

            temperature=0.8,
            max_tokens=250,
        )

        ai_reply = (
            completion
            .choices[0]
            .message
            .content
            .strip()
        )

        return {

            "reply":
            ai_reply,

            "products":
            matched_products

        }

    except Exception as e:

        return {

            "reply":
            "I'm having trouble accessing our collection right now. Please try again in a moment ✨",

            "products":
            []

        }
