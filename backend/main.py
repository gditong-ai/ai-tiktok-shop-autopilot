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


DATABASE_URL = os.getenv("DATABASE_URL")
DATA = Path("/app/data")
DATA.mkdir(parents=True, exist_ok=True)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")


app = FastAPI(title="AI TikTok Shop Autopilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def conn():
    return psycopg.connect(DATABASE_URL)


@app.on_event("startup")
def init_database():
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id UUID PRIMARY KEY,
                name TEXT NOT NULL,
                price NUMERIC,
                commission NUMERIC,
                url TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS trends (
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES products(id),
                score NUMERIC,
                momentum NUMERIC,
                competition NUMERIC,
                data JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id UUID PRIMARY KEY,
                product_id UUID REFERENCES products(id),
                variant TEXT,
                script JSONB,
                file_path TEXT,
                status TEXT DEFAULT 'draft',
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id UUID PRIMARY KEY,
                video_id UUID REFERENCES videos(id),
                scheduled_at TIMESTAMPTZ,
                status TEXT DEFAULT 'queued',
                external_post_id TEXT
            );
        """)

        c.commit()


class Product(BaseModel):
    name: str
    price: float | None = None
    commission: float | None = None
    url: str | None = None
    notes: str | None = None


class Evidence(BaseModel):
    evidence: dict = Field(default_factory=dict)


def ai(prompt: str):
    key = os.getenv("GEMINI_API_KEY")

    if not key:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is not configured"
        )

    client = genai.Client(api_key=key)

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )

    return response.text


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/products")
def products():
    with conn() as c:
        rows = c.execute("""
            SELECT id, name, price, commission, url, notes
            FROM products
            ORDER BY created_at DESC
        """).fetchall()

    result = []

    for r in rows:
        result.append({
            "id": str(r[0]),
            "name": r[1],
            "price": float(r[2]) if r[2] is not None else None,
            "commission": float(r[3]) if r[3] is not None else None,
            "url": r[4],
            "notes": r[5],
        })

    return result


@app.post("/products")
def add_product(p: Product):
    product_id = uuid.uuid4()

    with conn() as c:
        c.execute("""
            INSERT INTO products
            (id, name, price, commission, url, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            product_id,
            p.name,
            p.price,
            p.commission,
            p.url,
            p.notes,
        ))

        c.commit()

    return {"id": str(product_id)}


@app.post("/products/{pid}/trend")
def trend(pid: str, e: Evidence):

    with conn() as c:
        product = c.execute("""
            SELECT name, price, commission, url, notes
            FROM products
            WHERE id = %s
        """, (pid,)).fetchone()

    if not product:
        raise HTTPException(404, "Product not found")

    prompt = f"""
Analyze this TikTok Shop product using ONLY the supplied evidence.

Do not invent live metrics.

Return JSON only.

Required fields:
score
momentum
competition
decision
reasons
content_angles

Product:
{product}

Evidence:
{json.dumps(e.evidence, ensure_ascii=False)}
"""

    raw = ai(prompt)

    start = raw.find("{")
    end = raw.rfind("}")

    if start == -1 or end == -1:
        raise HTTPException(502, "Gemini did not return valid JSON")

    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        raise HTTPException(502, "Gemini returned invalid JSON")

    trend_id = uuid.uuid4()

    with conn() as c:
        c.execute("""
            INSERT INTO trends
            (id, product_id, score, momentum, competition, data)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            trend_id,
            pid,
            data.get("score", 0),
            data.get("momentum", 0),
            data.get("competition", 0),
            json.dumps(data, ensure_ascii=False),
        ))

        c.commit()

    return data


@app.post("/products/{pid}/scripts")
def scripts(pid: str):

    with conn() as c:
        product = c.execute("""
            SELECT name, price, commission, notes
            FROM products
            WHERE id = %s
        """, (pid,)).fetchone()

    if not product:
        raise HTTPException(404, "Product not found")

    prompt = f"""
Create 3 ORIGINAL Thai TikTok Shop video concepts.

Product:
{product}

Do not make unsupported:
- medical claims
- financial claims
- guaranteed results

Return JSON array only.

Each item must contain:

variant
hook
scenes
caption
hashtags
cta

Each scene must contain:

seconds
visual
voiceover
"""

    raw = ai(prompt)

    start = raw.find("[")
    end = raw.rfind("]")

    if start == -1 or end == -1:
        raise HTTPException(
            502,
            "Gemini did not return a valid JSON array"
        )

    try:
        concepts = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        raise HTTPException(
            502,
            "Gemini returned invalid JSON"
        )

    video_ids = []

    with conn() as c:
        for concept in concepts:

            video_id = uuid.uuid4()

            c.execute("""
                INSERT INTO videos
                (id, product_id, variant, script)
                VALUES (%s, %s, %s, %s)
            """, (
                video_id,
                pid,
                concept.get("variant", "variant"),
                json.dumps(concept, ensure_ascii=False),
            ))

            video_ids.append(str(video_id))

        c.commit()

    return {
        "video_ids": video_ids,
        "scripts": concepts,
    }


@app.get("/videos")
def videos():

    with conn() as c:
        rows = c.execute("""
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
        """).fetchall()

    return [
        {
            "id": str(r[0]),
            "product_id": str(r[1]),
            "product": r[2],
            "variant": r[3],
            "status": r[4],
        }
        for r in rows
    ]


@app.post("/videos/{vid}/render")
def render(vid: str):

    with conn() as c:
        row = c.execute("""
            SELECT script
            FROM videos
            WHERE id = %s
        """, (vid,)).fetchone()

    if not row:
        raise HTTPException(404, "Video not found")

    script = row[0]

    try:
        scenes = script.get("scenes", [])

        duration = sum(
            float(scene.get("seconds", 3))
            for scene in scenes
        )

        duration = max(5, min(60, duration))

    except Exception:
        duration = 15

    output = DATA / f"{vid}.mp4"

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
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with conn() as c:
        c.execute("""
            UPDATE videos
            SET file_path = %s,
                status = 'ready'
            WHERE id = %s
        """, (str(output), vid))

        c.commit()

    return {
        "file": str(output),
        "status": "ready",
        "duration": duration,
    }


@app.post("/queue/{vid}")
def enqueue(vid: str):

    with conn() as c:
        exists = c.execute("""
            SELECT id
            FROM videos
            WHERE id = %s
        """, (vid,)).fetchone()

    if not exists:
        raise HTTPException(404, "Video not found")

    queue_id = uuid.uuid4()

    with conn() as c:
        c.execute("""
            INSERT INTO queue
            (id, video_id, status)
            VALUES (%s, %s, 'queued')
        """, (queue_id, vid))

        c.commit()

    return {
        "id": str(queue_id),
        "status": "queued",
    }


@app.get("/queue")
def get_queue():

    with conn() as c:
        rows = c.execute("""
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
            ORDER BY q.scheduled_at NULLS LAST
        """).fetchall()

    return [
        {
            "id": str(r[0]),
            "video_id": str(r[1]),
            "status": r[2],
            "product": r[3],
            "variant": r[4],
        }
        for r in rows
    ]
