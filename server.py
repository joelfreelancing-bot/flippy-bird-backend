import asyncio
import sys
import os
import logging
import secrets
import jwt
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta

# Fix Windows Loop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from typing import List, Optional
from dotenv import load_dotenv

# --- CONFIG ---
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# ⚠️ KEEP A FIXED KEY so users stay logged in
SECRET_KEY = "flippy_bird_super_secret_key_forever"
ALGORITHM = "HS256"
# Note: In production, use os.getenv("MONGO_URI") instead of hardcoding
MONGO_URI = "mongodb+srv://joelfreelancing_db_user:WtZkkz8rkw21Wes2@flippybird.7rfhk80.mongodb.net/?appName=FlippyBird"

client = None
db = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, db
    print("🚀 Connecting to DB...")
    try:
        client = AsyncIOMotorClient(MONGO_URI)
        db = client.flippybird_db
        await client.admin.command('ping')
        print("✅ DB Connected!")
    except Exception as e:
        print(f"❌ DB Error: {e}")
    yield
    if client: client.close()

app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

# --- MODELS ---

class UserInit(BaseModel):
    device_id: str
    username: str

class User(BaseModel):
    device_id: str
    username: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ScoreSubmit(BaseModel):
    score: int

class LeaderboardEntry(BaseModel):
    username: str
    score: int
    rank: Optional[int] = None

# --- AUTH ROUTES ---

@api_router.post("/auth/init")
async def initialize_user(data: UserInit):
    if db is None: return JSONResponse(status_code=500, content={"message": "DB Error"})

    # 1. Check if this device already exists
    existing_user = await db.users.find_one({"device_id": data.device_id})
    
    if existing_user:
        # User exists, just return a token
        token = create_token(data.device_id, existing_user["username"])
        return {
            "message": "Welcome back",
            "access_token": token,
            "username": existing_user["username"],
            "new_user": False
        }
    
    # 2. New Device? Check if USERNAME is unique
    if await db.users.find_one({"username": data.username}):
        return JSONResponse(status_code=400, content={"message": "Username taken, choose another."})

    # 3. Create new user
    new_user = {
        "device_id": data.device_id,
        "username": data.username,
        "created_at": datetime.utcnow()
    }
    await db.users.insert_one(new_user)
    
    token = create_token(data.device_id, data.username)
    return {
        "message": "Profile created", 
        "access_token": token, 
        "username": data.username,
        "new_user": True
    }

# --- HELPERS ---
def create_token(device_id: str, username: str):
    expire = datetime.utcnow() + timedelta(days=3650) # 10 Years Expiry
    payload = {"sub": device_id, "name": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["sub"] # Returns device_id
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- SCORE ROUTES ---
@api_router.post("/scores/submit")
async def submit_score(data: ScoreSubmit, device_id: str = Depends(get_current_user)):
    user = await db.users.find_one({"device_id": device_id})
    if not user: raise HTTPException(status_code=404, detail="User not found")
    
    await db.scores.insert_one({
        "device_id": device_id,
        "username": user["username"],
        "score": data.score,
        "timestamp": datetime.utcnow()
    })
    return {"message": "Score saved"}

@api_router.get("/leaderboard/weekly")
async def weekly_leaderboard():
    pipeline = [
        {"$sort": {"score": -1}},
        {"$group": {
            "_id": "$device_id",
            "username": {"$first": "$username"},
            "score": {"$max": "$score"}
        }},
        {"$sort": {"score": -1}},
        {"$limit": 50}
    ]
    results = await db.scores.aggregate(pipeline).to_list(50)
    return [LeaderboardEntry(username=r["username"], score=r["score"], rank=i+1) for i, r in enumerate(results)]

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    # Use the PORT environment variable provided by Render, or default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)