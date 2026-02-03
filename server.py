import asyncio
import sys
import os
import jwt
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta

# Fix Windows Loop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, APIRouter, HTTPException, Depends
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
MONGO_URI = os.getenv("MONGO_URI")

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

class ScoreSubmit(BaseModel):
    score: int

class LeaderboardEntry(BaseModel):
    username: str
    score: int
    rank: Optional[int] = None

# --- AUTH ROUTES ---

@api_router.post("/auth/init")
async def initialize_user(data: UserInit):
    if db is None: 
        return JSONResponse(status_code=500, content={"detail": "Database not connected"})

    # --- 🔒 SECURITY STEP 1: Check if Name is Taken ---
    # We search case-insensitive ("Joel" == "joel")
    existing_name_user = await db.users.find_one(
        {"username": {"$regex": f"^{data.username}$", "$options": "i"}}
    )

    if existing_name_user:
        # The name exists. Now, IS IT YOU?
        if existing_name_user["device_id"] != data.device_id:
            # 🛑 STOP: Different device trying to use an existing name
            return JSONResponse(
                status_code=403, 
                content={"detail": "Username taken! Please choose another."}
            )
        
        # ✅ SUCCESS: It is you (re-install or same phone)
        token = create_token(data.device_id, existing_name_user["username"])
        return {
            "message": "Welcome back",
            "access_token": token,
            "username": existing_name_user["username"],
            "new_user": False
        }

    # --- SECURITY STEP 2: Check if Device already has a DIFFERENT name ---
    # (Optional: Prevent one phone from creating 100 accounts)
    existing_device_user = await db.users.find_one({"device_id": data.device_id})
    
    if existing_device_user:
        # You are 'Device 123' trying to be 'Mark', but you are already 'Joel'
        # We just log you in as your ORIGINAL name 'Joel'
        token = create_token(data.device_id, existing_device_user["username"])
        return {
            "message": "Restored previous account",
            "access_token": token,
            "username": existing_device_user["username"],
            "new_user": False
        }

    # --- STEP 3: Brand New User ---
    new_user = {
        "device_id": data.device_id,
        "username": data.username, # We save the exact casing they typed
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
    if not user: 
        raise HTTPException(status_code=404, detail="User not found")
    
    # Insert score record
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
        # Group by Device ID to only show each player's BEST score
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
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
