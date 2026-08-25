import os
import json
import uuid
import subprocess
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai


# ============================================================
# CONFIG
# ============================================================

DB = os.getenv("DATABASE_URL")
DATA = Path("/app/data")
DATA.mkdir(parents=True, exist_ok=True)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI TikTok Shop Autopilot",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE
# ============================================================

def conn():
    if not DB:
        raise RuntimeError(
            "DATABASE_URL is not configured"
        )

    return psycopg.connect(DB)


@app.on_event("startup")
def init():

    with conn() as c:

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS products(
                id UUID PRIMARY KEY,
                name TEXT NOT NULL,
                price NUMERIC,
                commission NUMERIC,
                url TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS trends(
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES products(id),
                score NUMERIC,
                momentum NUMERIC,
                competition NUMERIC,
                data JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS videos(
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES products(id),
                variant TEXT,
                script JSONB,
                file_path TEXT,
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS queue(
                id UUID PRIMARY KEY,
                video_id UUID REFERENCES videos(id),
                scheduled_at TIMESTAMPTZ,
                status TEXT DEFAULT 'queued',
                external_post_id TEXT
            );
            """
        )


# ============================================================
# MODELS
# ============================================================

class Product(BaseModel):

    name: str

    price: float | None = None

    commission: float | None = None

    url: str | None = None

    notes: str | None = None


class Evidence(BaseModel):

    evidence: dict = Field(
        default_factory=dict
    )


# ============================================================
# GEMINI
# ============================================================

def ai(prompt: str):

    key = os.getenv("GEMINI_API_KEY")

    if not key:

        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is not configured"
        )

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-2.5-flash"
    )

    try:

        print(
            f"Calling Gemini model: {model}",
            flush=True
        )

        client = genai.Client(
            api_key=key
        )

        response = client.models.generate_content(
            model=model,
            contents=prompt
        )

        if response is None:

            raise RuntimeError(
                "Gemini returned no response"
            )

        text = getattr(
            response,
            "text",
            None
        )

        if not text:

            raise RuntimeError(
                "Gemini returned empty text"
            )

        print(
            "Gemini request successful",
            flush=True
        )

        return text

    except HTTPException:
        raise

    except Exception as e:

        print(
            "================ GEMINI ERROR ================",
            flush=True
        )

        print(
            f"Type: {type(e).__name__}",
            flush=True
        )

        print(
            f"Error: {str(e)}",
            flush=True
        )

        print(
            "================================================",
            flush=True
        )

        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "Gemini API request failed",

                "error_type":
                    type(e).__name__,

                "error":
                    str(e)
            }
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True
    }


# ============================================================
# GEMINI TEST
# ============================================================

@app.get("/gemini-test")
def gemini_test():

    try:

        result = ai(
            "ตอบเพียงคำว่า OK"
        )

        return {
            "ok": True,
            "model": GEMINI_MODEL,
            "response": result
        }

    except HTTPException:
        raise

    except Exception as e:

        print(
            "GEMINI TEST ERROR:",
            type(e).__name__,
            str(e),
            flush=True
        )

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# ============================================================
# PRODUCTS
# ============================================================

@app.get("/products")
def products():

    with conn() as c:

        rows = c.execute(
            """
            SELECT
                id,
                name,
                price,
                commission,
                url,
                notes
            FROM products
            ORDER BY created_at DESC
            """
        ).fetchall()

    result = []

    for row in rows:

        result.append(
            {
                "id": str(row[0]),
                "name": row[1],
                "price": (
                    float(row[2])
                    if row[2] is not None
                    else None
                ),
                "commission": (
                    float(row[3])
                    if row[3] is not None
                    else None
                ),
                "url": row[4],
                "notes": row[5]
            }
        )

    return result


@app.post("/products")
def add_product(
    product: Product
):

    product_id = uuid.uuid4()

    with conn() as c:

        c.execute(
            """
            INSERT INTO products(
                id,
                name,
                price,
                commission,
                url,
                notes,
                created_at
            )
            VALUES(
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                now()
            )
            """,
            (
                product_id,
                product.name,
                product.price,
                product.commission,
                product.url,
                product.notes
            )
        )

    return {
        "id": str(product_id),
        "status": "created"
    }


# ============================================================
# TREND ANALYSIS
# ============================================================

@app.post("/products/{pid}/trend")
def trend(
    pid: str,
    evidence: Evidence
):

    try:

        product_id = uuid.UUID(pid)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Invalid product ID"
        )


    with conn() as c:

        product = c.execute(
            """
            SELECT
                name,
                price,
                commission,
                url,
                notes
            FROM products
            WHERE id=%s
            """,
            (product_id,)
        ).fetchone()


    if not product:

        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )


    prompt = f"""
Analyze this TikTok Shop product.

IMPORTANT:
- Use ONLY supplied evidence.
- Do not invent live TikTok metrics.
- Do not claim access to private TikTok data.
- Return JSON only.

Product:
{product}

Evidence:
{json.dumps(
    evidence.evidence,
    ensure_ascii=False
)}

Return:

{{
  "score": 0,
  "momentum": 0,
  "competition": 0,
  "decision": "",
  "reasons": [],
  "content_angles": []
}}
"""


    raw = ai(prompt)


    try:

        start = raw.find("{")
        end = raw.rfind("}")

        if start == -1 or end == -1:

            raise ValueError(
                "Gemini did not return JSON"
            )

        data = json.loads(
            raw[start:end + 1]
        )

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "Invalid JSON from Gemini",

                "error":
                    str(e),

                "raw":
                    raw[:2000]
            }
        )


    trend_id = uuid.uuid4()


    with conn() as c:

        c.execute(
            """
            INSERT INTO trends(
                id,
                product_id,
                score,
                momentum,
                competition,
                data,
                created_at
            )
            VALUES(
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                now()
            )
            """,
            (
                trend_id,
                product_id,
                data.get("score", 0),
                data.get("momentum", 0),
                data.get("competition", 0),
                json.dumps(
                    data,
                    ensure_ascii=False
                )
            )
        )


    return data


# ============================================================
# CREATE SCRIPTS
# ============================================================

@app.post("/products/{pid}/scripts")
def scripts(
    pid: str
):

    try:

        product_id = uuid.UUID(pid)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Invalid product ID"
        )


    with conn() as c:

        product = c.execute(
            """
            SELECT
                name,
                price,
                commission,
                notes
            FROM products
            WHERE id=%s
            """,
            (product_id,)
        ).fetchone()


    if not product:

        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )


    prompt = f"""
Create 3 ORIGINAL Thai TikTok Shop
short-video concepts.

Product:
{product}

Rules:

- Thai language.
- Original content.
- Do not copy another creator.
- Do not make unsupported medical claims.
- Do not make guaranteed income claims.
- Do not make guaranteed results claims.
- Suitable for TikTok Shop.
- Each video should be approximately
  15-30 seconds.
- Return JSON array only.

Each item must contain:

variant
hook
scenes

Each scene must contain:

seconds
visual
voiceover

Also include:

caption
hashtags
cta

JSON format:

[
  {{
    "variant": "A",
    "hook": "...",
    "scenes": [
      {{
        "seconds": 3,
        "visual": "...",
        "voiceover": "..."
      }}
    ],
    "caption": "...",
    "hashtags": ["#..."],
    "cta": "..."
  }}
]
"""


    raw = ai(prompt)


    try:

        start = raw.find("[")
        end = raw.rfind("]")

        if start == -1 or end == -1:

            raise ValueError(
                "Gemini did not return JSON array"
            )

        concepts = json.loads(
            raw[start:end + 1]
        )

        if not isinstance(
            concepts,
            list
        ):

            raise ValueError(
                "Gemini response is not an array"
            )

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "Invalid JSON from Gemini",

                "error":
                    str(e),

                "raw":
                    raw[:3000]
            }
        )


    video_ids = []


    with conn() as c:

        for concept in concepts:

            video_id = uuid.uuid4()

            variant = str(
                concept.get(
                    "variant",
                    "default"
                )
            )


            c.execute(
                """
                INSERT INTO videos(
                    id,
                    product_id,
                    variant,
                    script,
                    status,
                    created_at
                )
                VALUES(
                    %s,
                    %s,
                    %s,
                    %s,
                    'draft',
                    now()
                )
                """,
                (
                    video_id,
                    product_id,
                    variant,
                    json.dumps(
                        concept,
                        ensure_ascii=False
                    )
                )
            )


            video_ids.append(
                str(video_id)
            )


    return {
        "video_ids": video_ids,
        "scripts": concepts
    }


# ============================================================
# VIDEOS
# ============================================================

@app.get("/videos")
def videos():

    with conn() as c:

        rows = c.execute(
            """
            SELECT
                v.id,
                v.product_id,
                p.name,
                v.variant,
                v.status
            FROM videos v
            JOIN products p
              ON p.id = v.product_id
            ORDER BY v.created_at DESC
            """
        ).fetchall()


    result = []

    for row in rows:

        result.append(
            {
                "id": str(row[0]),
                "product_id": str(row[1]),
                "product": row[2],
                "variant": row[3],
                "status": row[4]
            }
        )

    return result


# ============================================================
# RENDER VIDEO
# ============================================================

@app.post("/videos/{vid}/render")
def render_video(
    vid: str
):

    try:

        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Invalid video ID"
        )


    with conn() as c:

        row = c.execute(
            """
            SELECT script
            FROM videos
            WHERE id=%s
            """,
            (video_id,)
        ).fetchone()


    if not row:

        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )


    script = row[0]


    try:

        scenes = script.get(
            "scenes",
            []
        )

        duration = sum(
            float(
                scene.get(
                    "seconds",
                    3
                )
            )
            for scene in scenes
        )

        duration = max(
            5,
            min(60, duration)
        )

    except Exception:

        duration = 15


    output = DATA / (
        f"{video_id}.mp4"
    )


    try:

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1080x1920:r=30",
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(output)
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail={
                "message":
                    "FFmpeg rendering failed",

                "error":
                    str(e)
            }
        )


    with conn() as c:

        c.execute(
            """
            UPDATE videos
            SET
                file_path=%s,
                status='ready'
            WHERE id=%s
            """,
            (
                str(output),
                video_id
            )
        )


    return {
        "file": str(output),
        "status": "ready"
    }


# ============================================================
# QUEUE
# ============================================================

@app.post("/queue/{vid}")
def enqueue(
    vid: str
):

    try:

        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Invalid video ID"
        )


    with conn() as c:

        video = c.execute(
            """
            SELECT id
            FROM videos
            WHERE id=%s
            """,
            (video_id,)
        ).fetchone()


    if not video:

        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )


    queue_id = uuid.uuid4()


    with conn() as c:

        c.execute(
            """
            INSERT INTO queue(
                id,
                video_id,
                status
            )
            VALUES(
                %s,
                %s,
                'queued'
            )
            """,
            (
                queue_id,
                video_id
            )
        )


    return {
        "id": str(queue_id),
        "video_id": str(video_id),
        "status": "queued"
    }


@app.get("/queue")
def get_queue():

    with conn() as c:

        rows = c.execute(
            """
            SELECT
                q.id,
                q.video_id,
                q.status,
                p.name,
                v.variant
            FROM queue q
            JOIN videos v
              ON v.id = q.video_id
            JOIN products p
              ON p.id = v.product_id
            ORDER BY
                q.scheduled_at NULLS LAST
            """
        ).fetchall()


    result = []

    for row in rows:

        result.append(
            {
                "id": str(row[0]),
                "video_id": str(row[1]),
                "status": row[2],
                "product": row[3],
                "variant": row[4]
            }
        )

    return result
