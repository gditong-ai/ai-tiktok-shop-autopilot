import os
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai


# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")


app = FastAPI(
    title="AI TikTok Shop Trend Autopilot",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# DATABASE
# =========================================================

def db():
    return psycopg.connect(DATABASE_URL)


@app.on_event("startup")
def startup():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_products (
                id UUID PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT,
                price NUMERIC,
                commission NUMERIC,
                product_url TEXT,
                image_url TEXT,
                source TEXT,
                trend_data JSONB DEFAULT '{}'::jsonb,
                trend_score NUMERIC DEFAULT 0,
                demand_score NUMERIC DEFAULT 0,
                competition_score NUMERIC DEFAULT 0,
                content_score NUMERIC DEFAULT 0,
                affiliate_score NUMERIC DEFAULT 0,
                ai_score NUMERIC DEFAULT 0,
                decision TEXT DEFAULT 'review',
                reasons JSONB DEFAULT '[]'::jsonb,
                content_angles JSONB DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_snapshots (
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES trend_products(id)
                    ON DELETE CASCADE,
                source TEXT,
                data JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS selected_products (
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES trend_products(id)
                    ON DELETE CASCADE,
                selected_by TEXT DEFAULT 'ai',
                reason TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES trend_products(id)
                    ON DELETE CASCADE,
                variant TEXT,
                script JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        conn.commit()


# =========================================================
# GEMINI
# =========================================================

def gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is not configured"
        )

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )

        if not response or not response.text:
            raise HTTPException(
                status_code=500,
                detail="Gemini returned an empty response"
            )

        return response.text

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Gemini API request failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }
        )


def extract_json(text: str) -> Any:
    """
    รองรับ Gemini ที่อาจตอบกลับมาเป็น ```json ... ```
    """

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # JSON object
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1

        try:
            return json.loads(text[start:end])
        except Exception:
            pass

    # JSON array
    if "[" in text and "]" in text:
        start = text.find("[")
        end = text.rfind("]") + 1

        try:
            return json.loads(text[start:end])
        except Exception:
            pass

    raise HTTPException(
        status_code=502,
        detail={
            "message": "Gemini did not return valid JSON",
            "raw": text[:2000]
        }
    )


# =========================================================
# MODELS
# =========================================================

class TrendProduct(BaseModel):
    name: str
    category: str | None = None
    price: float | None = None
    commission: float | None = None
    product_url: str | None = None
    image_url: str | None = None
    source: str = "manual_feed"

    # ข้อมูลที่ระบบได้รับจากแหล่งข้อมูลที่ได้รับอนุญาต
    trend_data: dict[str, Any] = Field(default_factory=dict)


class DiscoverRequest(BaseModel):
    products: list[TrendProduct]


class SelectRequest(BaseModel):
    product_id: str


class AutoSelectRequest(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


# =========================================================
# HEALTH
# =========================================================

@app.get("/")
def root():
    return {
        "name": "AI TikTok Shop Trend Autopilot",
        "version": "2.0.0",
        "status": "running"
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": GEMINI_MODEL,
        "time": datetime.now(timezone.utc).isoformat()
    }


# =========================================================
# TREND DISCOVERY
# =========================================================

@app.post("/trends/discover")
def discover_trends(request: DiscoverRequest):

    if not request.products:
        raise HTTPException(
            status_code=400,
            detail="No products supplied"
        )

    product_data = [
        {
            "name": p.name,
            "category": p.category,
            "price": p.price,
            "commission": p.commission,
            "product_url": p.product_url,
            "source": p.source,
            "trend_data": p.trend_data
        }
        for p in request.products
    ]

    prompt = f"""
You are an AI TikTok Shop affiliate trend analyst.

Analyze ONLY the supplied product/trend data.

IMPORTANT:
- Do NOT invent TikTok metrics.
- Do NOT claim that a product is trending on TikTok unless the supplied evidence supports it.
- Do NOT invent sales, views, orders or commission.
- If evidence is missing, lower the confidence.
- This is affiliate content analysis.
- Avoid medical, financial, guaranteed-result or misleading claims.

Score every product from 0-100 for:

1. trend_score
2. demand_score
3. competition_score
4. content_score
5. affiliate_score
6. ai_score

For competition_score:
100 = low competition / good opportunity
0 = extremely competitive

For the final ai_score, consider:

trend
demand
competition opportunity
content potential
affiliate commission
price attractiveness

Return JSON ONLY.

Format:

{{
  "products": [
    {{
      "name": "product name",
      "trend_score": 0,
      "demand_score": 0,
      "competition_score": 0,
      "content_score": 0,
      "affiliate_score": 0,
      "ai_score": 0,
      "decision": "strong_candidate|candidate|review|skip",
      "confidence": 0,
      "reasons": [],
      "content_angles": []
    }}
  ]
}}

PRODUCT DATA:

{json.dumps(product_data, ensure_ascii=False)}
"""

    result = extract_json(gemini(prompt))

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid AI trend response"
        )

    analyzed = result.get("products", [])

    saved = []

    with db() as conn:

        for item, original in zip(analyzed, request.products):

            product_id = uuid.uuid4()

            conn.execute(
                """
                INSERT INTO trend_products (
                    id,
                    name,
                    category,
                    price,
                    commission,
                    product_url,
                    image_url,
                    source,
                    trend_data,
                    trend_score,
                    demand_score,
                    competition_score,
                    content_score,
                    affiliate_score,
                    ai_score,
                    decision,
                    reasons,
                    content_angles,
                    updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,now()
                )
                """,
                (
                    product_id,
                    original.name,
                    original.category,
                    original.price,
                    original.commission,
                    original.product_url,
                    original.image_url,
                    original.source,
                    json.dumps(
                        original.trend_data,
                        ensure_ascii=False
                    ),
                    item.get("trend_score", 0),
                    item.get("demand_score", 0),
                    item.get("competition_score", 0),
                    item.get("content_score", 0),
                    item.get("affiliate_score", 0),
                    item.get("ai_score", 0),
                    item.get("decision", "review"),
                    json.dumps(
                        item.get("reasons", []),
                        ensure_ascii=False
                    ),
                    json.dumps(
                        item.get("content_angles", []),
                        ensure_ascii=False
                    )
                )
            )

            conn.execute(
                """
                INSERT INTO trend_snapshots (
                    id,
                    product_id,
                    source,
                    data
                )
                VALUES (%s,%s,%s,%s)
                """,
                (
                    uuid.uuid4(),
                    product_id,
                    original.source,
                    json.dumps(
                        original.trend_data,
                        ensure_ascii=False
                    )
                )
            )

            saved.append({
                "id": str(product_id),
                "name": original.name,
                **item
            })

        conn.commit()

    saved.sort(
        key=lambda x: float(x.get("ai_score", 0)),
        reverse=True
    )

    return {
        "success": True,
        "count": len(saved),
        "products": saved
    }


# =========================================================
# GET TREND PRODUCTS
# =========================================================

@app.get("/trends")
def get_trends():

    with db() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                name,
                category,
                price,
                commission,
                product_url,
                source,
                trend_score,
                demand_score,
                competition_score,
                content_score,
                affiliate_score,
                ai_score,
                decision,
                reasons,
                content_angles,
                created_at
            FROM trend_products
            ORDER BY ai_score DESC, created_at DESC
            """
        ).fetchall()

    result = []

    for r in rows:

        result.append({
            "id": str(r[0]),
            "name": r[1],
            "category": r[2],
            "price": float(r[3]) if r[3] is not None else None,
            "commission": float(r[4]) if r[4] is not None else None,
            "product_url": r[5],
            "source": r[6],
            "trend_score": float(r[7] or 0),
            "demand_score": float(r[8] or 0),
            "competition_score": float(r[9] or 0),
            "content_score": float(r[10] or 0),
            "affiliate_score": float(r[11] or 0),
            "ai_score": float(r[12] or 0),
            "decision": r[13],
            "reasons": r[14],
            "content_angles": r[15],
            "created_at": r[16]
        })

    return result


# =========================================================
# TOP TRENDING
# =========================================================

@app.get("/trends/top")
def top_trends(limit: int = 10):

    limit = max(1, min(limit, 50))

    with db() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                name,
                category,
                price,
                commission,
                trend_score,
                demand_score,
                competition_score,
                content_score,
                affiliate_score,
                ai_score,
                decision,
                reasons,
                content_angles
            FROM trend_products
            ORDER BY ai_score DESC
            LIMIT %s
            """,
            (limit,)
        ).fetchall()

    return [
        {
            "rank": i + 1,
            "id": str(r[0]),
            "name": r[1],
            "category": r[2],
            "price": float(r[3]) if r[3] is not None else None,
            "commission": float(r[4]) if r[4] is not None else None,
            "trend_score": float(r[5] or 0),
            "demand_score": float(r[6] or 0),
            "competition_score": float(r[7] or 0),
            "content_score": float(r[8] or 0),
            "affiliate_score": float(r[9] or 0),
            "ai_score": float(r[10] or 0),
            "decision": r[11],
            "reasons": r[12],
            "content_angles": r[13]
        }
        for i, r in enumerate(rows)
    ]


# =========================================================
# SELECT PRODUCT
# =========================================================

@app.post("/trends/{product_id}/select")
def select_product(product_id: str):

    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid product ID"
        )

    with db() as conn:

        product = conn.execute(
            """
            SELECT
                id,
                name,
                ai_score,
                decision
            FROM trend_products
            WHERE id=%s
            """,
            (pid,)
        ).fetchone()

        if not product:
            raise HTTPException(
                status_code=404,
                detail="Trend product not found"
            )

        conn.execute(
            """
            INSERT INTO selected_products (
                id,
                product_id,
                selected_by,
                reason
            )
            VALUES (%s,%s,%s,%s)
            """,
            (
                uuid.uuid4(),
                pid,
                "user",
                "Selected from AI trend dashboard"
            )
        )

        conn.commit()

    return {
        "success": True,
        "product_id": product_id,
        "name": product[1],
        "ai_score": float(product[2] or 0),
        "decision": product[3]
    }


# =========================================================
# AI AUTO SELECT
# =========================================================

@app.post("/trends/auto-select")
def auto_select(request: AutoSelectRequest):

    with db() as conn:

        products = conn.execute(
            """
            SELECT
                id,
                name,
                category,
                price,
                commission,
                trend_score,
                demand_score,
                competition_score,
                content_score,
                affiliate_score,
                ai_score,
                decision
            FROM trend_products
            WHERE decision IN (
                'strong_candidate',
                'candidate'
            )
            ORDER BY ai_score DESC
            LIMIT %s
            """,
            (request.limit,)
        ).fetchall()

        selected = []

        for p in products:

            product_id = p[0]

            # ป้องกันการเลือกซ้ำ
            exists = conn.execute(
                """
                SELECT 1
                FROM selected_products
                WHERE product_id=%s
                LIMIT 1
                """,
                (product_id,)
            ).fetchone()

            if exists:
                continue

            conn.execute(
                """
                INSERT INTO selected_products (
                    id,
                    product_id,
                    selected_by,
                    reason
                )
                VALUES (%s,%s,%s,%s)
                """,
                (
                    uuid.uuid4(),
                    product_id,
                    "ai",
                    f"AI selected product with score {p[10]}"
                )
            )

            selected.append({
                "id": str(product_id),
                "name": p[1],
                "ai_score": float(p[10] or 0),
                "decision": p[11]
            })

        conn.commit()

    return {
        "success": True,
        "selected": selected
    }


# =========================================================
# SELECTED PRODUCTS
# =========================================================

@app.get("/selected")
def get_selected():

    with db() as conn:

        rows = conn.execute(
            """
            SELECT
                s.id,
                s.product_id,
                p.name,
                p.category,
                p.price,
                p.commission,
                p.product_url,
                p.ai_score,
                s.selected_by,
                s.reason,
                s.created_at
            FROM selected_products s
            JOIN trend_products p
                ON p.id=s.product_id
            ORDER BY s.created_at DESC
            """
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "product_id": str(r[1]),
            "name": r[2],
            "category": r[3],
            "price": float(r[4]) if r[4] is not None else None,
            "commission": float(r[5]) if r[5] is not None else None,
            "product_url": r[6],
            "ai_score": float(r[7] or 0),
            "selected_by": r[8],
            "reason": r[9],
            "created_at": r[10]
        }
        for r in rows
    ]


# =========================================================
# AI SCRIPT GENERATOR
# =========================================================

@app.post("/trends/{product_id}/scripts")
def generate_scripts(product_id: str):

    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid product ID"
        )

    with db() as conn:

        product = conn.execute(
            """
            SELECT
                name,
                category,
                price,
                commission,
                product_url,
                trend_data,
                trend_score,
                demand_score,
                competition_score,
                content_score,
                affiliate_score,
                ai_score,
                content_angles
            FROM trend_products
            WHERE id=%s
            """,
            (pid,)
        ).fetchone()

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    product_info = {
        "name": product[0],
        "category": product[1],
        "price": product[2],
        "commission": product[3],
        "product_url": product[4],
        "trend_data": product[5],
        "trend_score": product[6],
        "demand_score": product[7],
        "competition_score": product[8],
        "content_score": product[9],
        "affiliate_score": product[10],
        "ai_score": product[11],
        "content_angles": product[12]
    }

    prompt = f"""
You are a professional Thai TikTok Shop affiliate content creator.

Create 3 ORIGINAL short-form video concepts for this product.

IMPORTANT:
- Do not invent product specifications.
- Do not make medical claims.
- Do not make guaranteed-result claims.
- Do not claim fake discounts.
- Do not fabricate reviews.
- Use only the supplied product information.
- Make the content suitable for affiliate marketing.
- The viewer should understand why they may want to check the product.
- Do not pretend to be the product owner.
- Use natural Thai language.

Each concept must contain:

variant
hook
target_audience
scenes
voiceover
caption
hashtags
cta

Scenes should be 5-8 scenes.

Each scene must contain:

seconds
visual
voiceover
text_overlay

Return JSON ONLY:

{{
  "scripts": [
    {{
      "variant": "A",
      "hook": "...",
      "target_audience": "...",
      "scenes": [
        {{
          "seconds": 3,
          "visual": "...",
          "voiceover": "...",
          "text_overlay": "..."
        }}
      ],
      "caption": "...",
      "hashtags": ["...", "..."],
      "cta": "..."
    }}
  ]
}}

PRODUCT:

{json.dumps(product_info, ensure_ascii=False, default=str)}
"""

    result = extract_json(gemini(prompt))

    scripts = result.get("scripts", [])

    if not scripts:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned no scripts"
        )

    saved = []

    with db() as conn:

        for script in scripts:

            script_id = uuid.uuid4()

            conn.execute(
                """
                INSERT INTO scripts (
                    id,
                    product_id,
                    variant,
                    script
                )
                VALUES (%s,%s,%s,%s)
                """,
                (
                    script_id,
                    pid,
                    script.get("variant", "A"),
                    json.dumps(
                        script,
                        ensure_ascii=False
                    )
                )
            )

            saved.append({
                "id": str(script_id),
                **script
            })

        conn.commit()

    return {
        "success": True,
        "product_id": product_id,
        "scripts": saved
    }


# =========================================================
# GET SCRIPTS
# =========================================================

@app.get("/scripts")
def get_scripts():

    with db() as conn:

        rows = conn.execute(
            """
            SELECT
                s.id,
                s.product_id,
                p.name,
                s.variant,
                s.script,
                s.created_at
            FROM scripts s
            JOIN trend_products p
                ON p.id=s.product_id
            ORDER BY s.created_at DESC
            """
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "product_id": str(r[1]),
            "product": r[2],
            "variant": r[3],
            "script": r[4],
            "created_at": r[5]
        }
        for r in rows
    ]


# =========================================================
# DELETE OLD TREND DATA
# =========================================================

@app.delete("/trends/clear")
def clear_trends():

    with db() as conn:

        conn.execute("DELETE FROM trend_products")
        conn.commit()

    return {
        "success": True,
        "message": "Trend database cleared"
    }
