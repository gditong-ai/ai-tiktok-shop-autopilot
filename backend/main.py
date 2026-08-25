import os
import json
import uuid
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import httpx
import psycopg

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

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
    "gemini-3.6-flash"
)

TIKTOK_CLIENT_KEY = os.getenv(
    "TIKTOK_CLIENT_KEY"
)

TIKTOK_CLIENT_SECRET = os.getenv(
    "TIKTOK_CLIENT_SECRET"
)

TIKTOK_REDIRECT_URI = os.getenv(
    "TIKTOK_REDIRECT_URI"
)

TIKTOK_AUTH_URL = (
    "https://www.tiktok.com/v2/auth/authorize/"
)

TIKTOK_TOKEN_URL = (
    "https://open.tiktokapis.com/v2/oauth/token/"
)

TIKTOK_CREATOR_INFO_URL = (
    "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
)

TIKTOK_DIRECT_POST_URL = (
    "https://open.tiktokapis.com/v2/post/publish/video/init/"
)

TIKTOK_UPLOAD_URL = (
    "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
)

TIKTOK_STATUS_URL = (
    "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI TikTok Shop Autopilot",
    version="0.2.0"
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

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tiktok_accounts(
                id UUID PRIMARY KEY,
                open_id TEXT UNIQUE,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at TIMESTAMPTZ,
                refresh_expires_at TIMESTAMPTZ,
                scope TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tiktok_publish(
                id UUID PRIMARY KEY,
                video_id UUID REFERENCES videos(id),
                account_id UUID REFERENCES tiktok_accounts(id),
                publish_id TEXT,
                mode TEXT,
                status TEXT,
                response JSONB,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
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


class TikTokPublishRequest(BaseModel):

    title: str | None = None

    privacy_level: str | None = None

    disable_comment: bool = False

    disable_duet: bool = False

    disable_stitch: bool = False


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

    try:

        client = genai.Client(
            api_key=key
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
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

        return text

    except HTTPException:
        raise

    except Exception as e:

        print(
            "GEMINI ERROR:",
            type(e).__name__,
            str(e),
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

@app.get("/")
def root():

    return {
        "name":
            "AI TikTok Shop Autopilot",
        "version":
            "0.2.0",
        "status":
            "online"
    }


@app.get("/health")
def health():

    return {
        "ok": True
    }


@app.get("/gemini-test")
def gemini_test():

    result = ai(
        "ตอบเพียงคำว่า OK"
    )

    return {
        "ok": True,
        "model": GEMINI_MODEL,
        "response": result
    }


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

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "price":
                float(row[2])
                if row[2] is not None
                else None,
            "commission":
                float(row[3])
                if row[3] is not None
                else None,
            "url": row[4],
            "notes": row[5]
        }
        for row in rows
    ]


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
                notes
            )
            VALUES(
                %s,%s,%s,%s,%s,%s
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
# TREND
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
            400,
            "Invalid product ID"
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
            404,
            "Product not found"
        )

    prompt = f"""
Analyze this TikTok Shop product.

Use ONLY supplied evidence.
Do not invent live metrics.

Product:
{product}

Evidence:
{json.dumps(
    evidence.evidence,
    ensure_ascii=False
)}

Return JSON only:

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

        data = json.loads(
            raw[start:end + 1]
        )

    except Exception as e:

        raise HTTPException(
            502,
            {
                "message":
                    "Invalid Gemini JSON",
                "error":
                    str(e)
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
                data
            )
            VALUES(
                %s,%s,%s,%s,%s,%s
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
# SCRIPTS
# ============================================================

@app.post("/products/{pid}/scripts")
def scripts(pid: str):

    try:
        product_id = uuid.UUID(pid)

    except ValueError:

        raise HTTPException(
            400,
            "Invalid product ID"
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
            404,
            "Product not found"
        )

    prompt = f"""
Create 3 original Thai TikTok Shop
video concepts.

Product:
{product}

Rules:
- Thai language
- Original content
- No unsupported medical claims
- No guaranteed income claims
- No guaranteed results
- Suitable for TikTok Shop

Return JSON array only.

Each item:

variant
hook
scenes
caption
hashtags
cta

Each scene:

seconds
visual
voiceover
"""

    raw = ai(prompt)

    try:

        start = raw.find("[")
        end = raw.rfind("]")

        concepts = json.loads(
            raw[start:end + 1]
        )

    except Exception as e:

        raise HTTPException(
            502,
            {
                "message":
                    "Invalid Gemini JSON",
                "error":
                    str(e),
                "raw":
                    raw[:2000]
            }
        )

    video_ids = []

    with conn() as c:

        for concept in concepts:

            video_id = uuid.uuid4()

            c.execute(
                """
                INSERT INTO videos(
                    id,
                    product_id,
                    variant,
                    script,
                    status
                )
                VALUES(
                    %s,%s,%s,%s,'draft'
                )
                """,
                (
                    video_id,
                    product_id,
                    str(
                        concept.get(
                            "variant",
                            "A"
                        )
                    ),
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
              ON p.id=v.product_id
            ORDER BY v.created_at DESC
            """
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "product_id": str(row[1]),
            "product": row[2],
            "variant": row[3],
            "status": row[4]
        }
        for row in rows
    ]


# ============================================================
# RENDER
# ============================================================

@app.post("/videos/{vid}/render")
def render_video(vid: str):

    try:
        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            400,
            "Invalid video ID"
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
            404,
            "Video not found"
        )

    script = row[0]

    try:

        duration = sum(
            float(
                x.get("seconds", 3)
            )
            for x in script.get(
                "scenes",
                []
            )
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
            500,
            {
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
# TIKTOK CONFIG CHECK
# ============================================================

def check_tiktok_config():

    missing = []

    if not TIKTOK_CLIENT_KEY:
        missing.append(
            "TIKTOK_CLIENT_KEY"
        )

    if not TIKTOK_CLIENT_SECRET:
        missing.append(
            "TIKTOK_CLIENT_SECRET"
        )

    if not TIKTOK_REDIRECT_URI:
        missing.append(
            "TIKTOK_REDIRECT_URI"
        )

    if missing:

        raise HTTPException(
            500,
            {
                "message":
                    "TikTok API is not configured",
                "missing":
                    missing
            }
        )


# ============================================================
# TIKTOK OAUTH
# ============================================================

@app.get("/tiktok/login")
def tiktok_login():

    check_tiktok_config()

    state = secrets.token_urlsafe(32)

    scopes = (
        "user.info.basic,"
        "video.upload,"
        "video.publish"
    )

    params = {
        "client_key":
            TIKTOK_CLIENT_KEY,

        "response_type":
            "code",

        "scope":
            scopes,

        "redirect_uri":
            TIKTOK_REDIRECT_URI,

        "state":
            state
    }

    url = (
        TIKTOK_AUTH_URL
        + "?"
        + urlencode(params)
    )

    return RedirectResponse(
        url=url
    )


@app.get("/tiktok/callback")
async def tiktok_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None
):

    if error:

        raise HTTPException(
            400,
            f"TikTok OAuth error: {error}"
        )

    if not code:

        raise HTTPException(
            400,
            "Missing OAuth code"
        )

    check_tiktok_config()

    payload = {
        "client_key":
            TIKTOK_CLIENT_KEY,

        "client_secret":
            TIKTOK_CLIENT_SECRET,

        "code":
            code,

        "grant_type":
            "authorization_code",

        "redirect_uri":
            TIKTOK_REDIRECT_URI
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        response = await client.post(
            TIKTOK_TOKEN_URL,
            data=payload
        )

    if response.status_code >= 400:

        raise HTTPException(
            502,
            {
                "message":
                    "TikTok token exchange failed",
                "response":
                    response.text
            }
        )

    data = response.json()

    access_token = data.get(
        "access_token"
    )

    refresh_token = data.get(
        "refresh_token"
    )

    open_id = data.get(
        "open_id"
    )

    if not access_token or not open_id:

        raise HTTPException(
            502,
            {
                "message":
                    "TikTok did not return required OAuth data",
                "response":
                    data
            }
        )

    with conn() as c:

        account = c.execute(
            """
            SELECT id
            FROM tiktok_accounts
            WHERE open_id=%s
            """,
            (open_id,)
        ).fetchone()

        if account:

            account_id = account[0]

            c.execute(
                """
                UPDATE tiktok_accounts
                SET
                    access_token=%s,
                    refresh_token=%s,
                    scope=%s,
                    updated_at=now()
                WHERE id=%s
                """,
                (
                    access_token,
                    refresh_token,
                    data.get("scope"),
                    account_id
                )
            )

        else:

            account_id = uuid.uuid4()

            c.execute(
                """
                INSERT INTO tiktok_accounts(
                    id,
                    open_id,
                    access_token,
                    refresh_token,
                    scope
                )
                VALUES(
                    %s,%s,%s,%s,%s
                )
                """,
                (
                    account_id,
                    open_id,
                    access_token,
                    refresh_token,
                    data.get("scope")
                )
            )

    return {
        "ok": True,
        "message":
            "TikTok account connected",
        "account_id":
            str(account_id)
    }


# ============================================================
# TIKTOK ACCOUNT STATUS
# ============================================================

@app.get("/tiktok/status")
async def tiktok_status():

    with conn() as c:

        row = c.execute(
            """
            SELECT
                id,
                open_id,
                scope,
                updated_at
            FROM tiktok_accounts
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:

        return {
            "connected": False
        }

    return {
        "connected": True,
        "account_id": str(row[0]),
        "open_id": row[1],
        "scope": row[2],
        "updated_at": str(row[3])
    }


# ============================================================
# GET TIKTOK ACCOUNT
# ============================================================

def get_tiktok_account():

    with conn() as c:

        row = c.execute(
            """
            SELECT
                id,
                access_token,
                open_id
            FROM tiktok_accounts
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:

        raise HTTPException(
            400,
            "TikTok account is not connected"
        )

    return {
        "id": row[0],
        "access_token": row[1],
        "open_id": row[2]
    }


# ============================================================
# CREATOR INFO
# ============================================================

@app.get("/tiktok/creator-info")
async def creator_info():

    account = get_tiktok_account()

    headers = {
        "Authorization":
            f"Bearer {account['access_token']}"
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        response = await client.post(
            TIKTOK_CREATOR_INFO_URL,
            headers=headers
        )

    if response.status_code >= 400:

        raise HTTPException(
            response.status_code,
            {
                "message":
                    "TikTok Creator Info failed",
                "response":
                    response.text
            }
        )

    return response.json()


# ============================================================
# DIRECT POST
# ============================================================

@app.post(
    "/videos/{vid}/tiktok/publish"
)
async def tiktok_publish(
    vid: str,
    request: TikTokPublishRequest
):

    try:
        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            400,
            "Invalid video ID"
        )

    account = get_tiktok_account()

    with conn() as c:

        video = c.execute(
            """
            SELECT
                id,
                file_path,
                status
            FROM videos
            WHERE id=%s
            """,
            (video_id,)
        ).fetchone()

    if not video:

        raise HTTPException(
            404,
            "Video not found"
        )

    file_path = video[1]

    if not file_path:

        raise HTTPException(
            400,
            "Video has not been rendered"
        )

    if not Path(file_path).exists():

        raise HTTPException(
            400,
            "Rendered video file does not exist"
        )

    creator = await creator_info()

    privacy_options = creator.get(
        "data",
        {}
    ).get(
        "privacy_level_options",
        []
    )

    privacy = (
        request.privacy_level
        or (
            privacy_options[0]
            if privacy_options
            else "SELF_ONLY"
        )
    )

    payload = {
        "post_info": {
            "title":
                request.title or "",
            "privacy_level":
                privacy,
            "disable_comment":
                request.disable_comment,
            "disable_duet":
                request.disable_duet,
            "disable_stitch":
                request.disable_stitch
        },
        "source_info": {
            "source":
                "FILE_UPLOAD",
            "video_size":
                Path(file_path).stat().st_size,
            "chunk_size":
                Path(file_path).stat().st_size,
            "total_chunk_count":
                1
        }
    }

    headers = {
        "Authorization":
            f"Bearer {account['access_token']}",
        "Content-Type":
            "application/json"
    }

    async with httpx.AsyncClient(
        timeout=60
    ) as client:

        response = await client.post(
            TIKTOK_DIRECT_POST_URL,
            headers=headers,
            json=payload
        )

    if response.status_code >= 400:

        raise HTTPException(
            response.status_code,
            {
                "message":
                    "TikTok Direct Post initialization failed",
                "response":
                    response.text
            }
        )

    data = response.json()

    publish_id = (
        data.get("data", {})
        .get("publish_id")
    )

    if not publish_id:

        raise HTTPException(
            502,
            {
                "message":
                    "TikTok did not return publish_id",
                "response":
                    data
            }
        )

    publish_record_id = uuid.uuid4()

    with conn() as c:

        c.execute(
            """
            INSERT INTO tiktok_publish(
                id,
                video_id,
                account_id,
                publish_id,
                mode,
                status,
                response
            )
            VALUES(
                %s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                publish_record_id,
                video_id,
                account["id"],
                publish_id,
                "direct",
                "processing",
                json.dumps(
                    data,
                    ensure_ascii=False
                )
            )
        )

    return {
        "ok": True,
        "mode": "direct",
        "publish_id":
            publish_id,
        "record_id":
            str(publish_record_id),
        "tiktok":
            data
    }


# ============================================================
# UPLOAD DRAFT
# ============================================================

@app.post(
    "/videos/{vid}/tiktok/upload"
)
async def tiktok_upload(
    vid: str
):

    try:
        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            400,
            "Invalid video ID"
        )

    account = get_tiktok_account()

    with conn() as c:

        video = c.execute(
            """
            SELECT
                id,
                file_path
            FROM videos
            WHERE id=%s
            """,
            (video_id,)
        ).fetchone()

    if not video:

        raise HTTPException(
            404,
            "Video not found"
        )

    file_path = video[1]

    if not file_path:

        raise HTTPException(
            400,
            "Video has not been rendered"
        )

    path = Path(file_path)

    if not path.exists():

        raise HTTPException(
            400,
            "Video file does not exist"
        )

    size = path.stat().st_size

    payload = {
        "source_info": {
            "source":
                "FILE_UPLOAD",
            "video_size":
                size,
            "chunk_size":
                size,
            "total_chunk_count":
                1
        }
    }

    headers = {
        "Authorization":
            f"Bearer {account['access_token']}",
        "Content-Type":
            "application/json"
    }

    async with httpx.AsyncClient(
        timeout=60
    ) as client:

        response = await client.post(
            TIKTOK_UPLOAD_URL,
            headers=headers,
            json=payload
        )

    if response.status_code >= 400:

        raise HTTPException(
            response.status_code,
            {
                "message":
                    "TikTok draft upload initialization failed",
                "response":
                    response.text
            }
        )

    data = response.json()

    publish_id = (
        data.get("data", {})
        .get("publish_id")
    )

    upload_url = (
        data.get("data", {})
        .get("upload_url")
    )

    record_id = uuid.uuid4()

    with conn() as c:

        c.execute(
            """
            INSERT INTO tiktok_publish(
                id,
                video_id,
                account_id,
                publish_id,
                mode,
                status,
                response
            )
            VALUES(
                %s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                record_id,
                video_id,
                account["id"],
                publish_id,
                "draft",
                "processing",
                json.dumps(
                    data,
                    ensure_ascii=False
                )
            )
        )

    return {
        "ok": True,
        "mode": "draft",
        "publish_id":
            publish_id,
        "upload_url":
            upload_url,
        "record_id":
            str(record_id),
        "tiktok":
            data
    }


# ============================================================
# PUBLISH STATUS
# ============================================================

@app.get(
    "/tiktok/publish/{publish_id}"
)
async def publish_status(
    publish_id: str
):

    account = get_tiktok_account()

    headers = {
        "Authorization":
            f"Bearer {account['access_token']}",
        "Content-Type":
            "application/json"
    }

    payload = {
        "publish_id":
            publish_id
    }

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        response = await client.post(
            TIKTOK_STATUS_URL,
            headers=headers,
            json=payload
        )

    if response.status_code >= 400:

        raise HTTPException(
            response.status_code,
            {
                "message":
                    "TikTok publish status failed",
                "response":
                    response.text
            }
        )

    data = response.json()

    status = (
        data.get("data", {})
        .get("status")
    )

    with conn() as c:

        c.execute(
            """
            UPDATE tiktok_publish
            SET
                status=%s,
                response=%s,
                updated_at=now()
            WHERE publish_id=%s
            """,
            (
                status or "unknown",
                json.dumps(
                    data,
                    ensure_ascii=False
                ),
                publish_id
            )
        )

    return data


# ============================================================
# TIKTOK PUBLISH HISTORY
# ============================================================

@app.get("/tiktok/publishes")
def tiktok_publishes():

    with conn() as c:

        rows = c.execute(
            """
            SELECT
                id,
                video_id,
                publish_id,
                mode,
                status,
                created_at,
                updated_at
            FROM tiktok_publish
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "video_id": str(row[1]),
            "publish_id": row[2],
            "mode": row[3],
            "status": row[4],
            "created_at": str(row[5]),
            "updated_at": str(row[6])
        }
        for row in rows
    ]


# ============================================================
# QUEUE
# ============================================================

@app.post("/queue/{vid}")
def enqueue(vid: str):

    try:
        video_id = uuid.UUID(vid)

    except ValueError:

        raise HTTPException(
            400,
            "Invalid video ID"
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
            404,
            "Video not found"
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
                %s,%s,'queued'
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
              ON v.id=q.video_id
            JOIN products p
              ON p.id=v.product_id
            ORDER BY
                q.scheduled_at NULLS LAST
            """
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "video_id": str(row[1]),
            "status": row[2],
            "product": row[3],
            "variant": row[4]
        }
        for row in rows
    ]
