from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import requests
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
    try:
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
    except Exception:
        return []


# ── Price Extractor ───────────────────────────────────────────────────────────
def extract_budget(query: str):
    q = query.lower().replace(",", "")

    def parse_amount(s: str) -> float:
        s = s.strip()
        m = re.match(r"(\d+(?:\.\d+)?)\s*k$", s)
        if m:
            return float(m.group(1)) * 1000
        return float(s)

    m = re.search(r"between\s+(\d[\d.]*k?)\s+(?:and|to|-)\s+(\d[\d.]*k?)", q)
    if m:
        return parse_amount(m.group(1)), parse_amount(m.group(2))

    m = re.search(
        r"(?:under|below|less\s+than|within|upto|up\s+to|max(?:imum)?|budget\s+(?:of|is)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        return None, parse_amount(m.group(1))

    m = re.search(
        r"(?:above|more\s+than|over|min(?:imum)?|starting\s+(?:from|at)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        return parse_amount(m.group(1)), None

    m = re.search(
        r"(?:around|approximately|about|near(?:ly)?)\s*(?:rs\.?|₹)?\s*(\d[\d.]*k?)",
        q
    )
    if m:
        center = parse_amount(m.group(1))
        return center * 0.8, center * 1.2

    return None, None


# ── Intent Maps ───────────────────────────────────────────────────────────────
def is_pure_greeting(query: str) -> bool:
    q = query.strip().lower().strip("?!.,")
    greetings = {
        "hi", "hello", "hey", "hey there", "good morning", "good afternoon", 
        "good evening", "wasup", "whats up", "greetings", "anyone there", "yo"
    }
    return q in greetings

CONCEPT_SYNONYMS = {
    "kids": ["baby", "boy", "girl", "child", "infant", "musical"],
    "kid": ["baby", "boy", "girl", "child", "infant", "musical"],
    "child": ["baby", "boy", "girl", "child", "infant"],
    "gifting": ["gift", "box", "set", "present", "packaging", "coin"],
    "giftbox": ["gift", "box", "set", "packaging", "coin box"]
}


# ── Strict Engine Matcher ─────────────────────────────────────────────────────
def match_products(user_message: str, products: list):
    query = user_message.lower().strip()
    
    # Clean string extraction for precise exact match passes
    clean_query_phrase = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', query)).strip()

    # 1. Budget extraction
    min_price, max_price = extract_budget(query)
    budget_info = ""
    if max_price is not None and min_price is None:
        budget_info = f"Budget restriction: Under Rs.{int(max_price):,}."
    elif min_price is not None and max_price is None:
        budget_info = f"Budget restriction: Above Rs.{int(min_price):,}."
    elif min_price is not None and max_price is not None:
        budget_info = f"Budget restriction: Between Rs.{int(min_price):,} and Rs.{int(max_price):,}."

    def in_budget(p):
        try:
            price = float(p["price"])
        except (ValueError, TypeError):
            return True
        if min_price is not None and price < min_price:
            return False
        if max_price is not None and price > max_price:
            return False
        return True

    pool = [p for p in products if in_budget(p)]
    if not pool:
        pool = products

    # TIER 1: Strict Sequence Substring Check (Catches full title snippets)
    # If they typed "999 Silver" or "Coin Box", find titles containing exactly that phrase segment.
    strict_phrase_matches = []
    if len(clean_query_phrase) > 3:
        # Check chunks or full user string against the catalog items
        for p in pool:
            title_clean = re.sub(r'[^\w\s]', ' ', p["title"].lower())
            if clean_query_phrase in title_clean or title_clean in clean_query_phrase:
                strict_phrase_matches.append(p)
                
        # If we have strict sequential text matches, return immediately. Bypasses fallback noise.
        if strict_phrase_matches:
            return strict_phrase_matches[:3], budget_info

    # TIER 2: Intelligent Multi-Keyword Scoring System
    words = clean_query_phrase.split()
    stopwords = {
        "i", "me", "my", "want", "need", "looking", "for", "a", "an", "the",
        "some", "any", "please", "can", "you", "show", "suggest", "recommend",
        "something", "is", "are", "do", "have", "get", "find", "what", "which",
        "best", "good", "nice", "beautiful", "pretty", "love", "like",
        "under", "below", "above", "less", "more", "than", "budget", "price",
        "cheap", "expensive", "affordable", "within", "around", "between"
    }
    keywords = [w for w in words if w not in stopwords and len(w) > 1]

    expanded_keywords = set(keywords)
    for kw in keywords:
        if kw in CONCEPT_SYNONYMS:
            expanded_keywords.update(CONCEPT_SYNONYMS[kw])

    scored_products = []
    for p in pool:
        title = p["title"].lower()
        body = p["description"].lower()
        tags = [t.lower() for t in p["tags"]] if isinstance(p["tags"], list) else []

        score = 0
        matched_title_tokens = 0

        for kw in expanded_keywords:
            if kw in title:
                score += 50
                matched_title_tokens += 1
            elif any(kw in tag for tag in tags):
                score += 20
                matched_title_tokens += 1
            elif kw in body:
                score += 5

        # Give dynamic multipliers if multiple structural words hit the same product title layout
        if len(keywords) > 1 and matched_title_tokens >= 2:
            score += 300

        if score > 0:
            scored_products.append((score, p))

    scored_products.sort(key=lambda x: x[0], reverse=True)
    
    # Filter and extract matches above a strict structural confidence floor value
    matched = [item[1] for item in scored_products if item[0] >= 40][:3]
    return matched, budget_info


# ── Chat Endpoint ─────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        user_msg = req.message.strip()
        
        # PIPELINE 1: Greeting Handshake
        if is_pure_greeting(user_msg):
            system_prompt = (
                "You are the elegant AI boutique concierge for *Taravya*, a premium sterling silver jewellery brand from India. "
                "The user just greeted you. Respond with a warm, sophisticated welcome (2-3 sentences max). "
                "Ask how you can assist them today with choosing the perfect jewellery piece or finding an elegant gift. "
                "Do NOT recommend or list specific products right now. Speak in elegant, flowing prose."
            )
            
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.7,
                max_tokens=150,
            )
            return {
                "reply": completion.choices[0].message.content.strip(),
                "products": []
            }

        # PIPELINE 2: Strict Precision Matching Engine
        products = get_products()
        matched_products, budget_info = match_products(user_msg, products)

        if matched_products:
            product_context = "\n".join([
                f"- **{p['title']}** -- Rs.{float(p['price']):,.0f} | Description: {p['description'][:100]}"
                for p in matched_products
            ])
            
            system_prompt = f"""You are the AI concierge for *Taravya*, an exquisite sterling silver jewellery brand from India.
Your persona: refined, warm, knowledgeable — like a trusted personal jeweller.

Your role is to present the matched items provided below to the customer with genuine luxury styling flair. Explain beautifully why these match what they asked for.

BUDGET / CONSTRAINT CONTEXT:
{budget_info}

EXACT MATCHED PIECES FROM CATALOG:
{product_context}

STRICT RULES:
- Talk ONLY about the products explicitly listed above. Do not bring up or invent other names.
- Keep your response to 3-5 sentences maximum in flowing, premium prose.
- Do NOT use bullet points, numbered lists, URLs, links, or image brackets.
- Use markdown: **bold** for product names, *italics* for descriptive styling details.
- End with a gentle question helping them finalize their choice."""

        else:
            system_prompt = f"""You are the AI concierge for *Taravya*, an exquisite sterling silver jewellery brand from India.
The customer is asking about '{user_msg}', but currently no products perfectly match this description in our active inventory lines.

Politely and beautifully inform them that we don't have that exact piece or collection variant available today, and gracefully invite them to explore your timeless silver rings, earrings, pendants, or customizable gifting suites instead. Do not show or invent any product data fields."""

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.6,
            max_tokens=250,
        )

        return {
            "reply": completion.choices[0].message.content.strip(),
            "products": matched_products,
        }

    except Exception as e:
        return {
            "reply": "I apologize, but I encountered a momentary hiccup while viewing our collection. May I assist you with anything else?",
            "products": [],
        }
