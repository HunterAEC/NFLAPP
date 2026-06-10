import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, cast
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from nflapp import NFLApp, HotTake
from models import DBUser, DBHotTake

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nfl_engine, db, api, crypto
    nfl_engine = NFLApp()
    db = nfl_engine.db
    api = nfl_engine.api
    crypto = nfl_engine.crypto
    await db.create_tables()
    logger.info("NFL Core App Web Engine initialized inside active loop.")
    yield
    await api.close()
    logger.info("Network pipelines closed safely.")

app = FastAPI(title="NFL Core App Web Engine API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserAuth(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)


class HotTakeCreate(BaseModel):
    username: str
    text: str


class VoteRequest(BaseModel):
    id: int
    type: Literal["up", "down"]


class PredictionCreate(BaseModel):
    username: str
    game_id: str
    game_name: str = "NFL Game"
    home: int
    away: int


class SettleRequest(BaseModel):
    game_id: str
    home: int
    away: int


@app.post("/users")
async def register(data: UserAuth):
    async with db.async_session() as session:
        res = await session.execute(select(DBUser).where(DBUser.username == data.username.strip()))
        existing = res.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        hashed = crypto.hash_password(data.password)
        session.add(
            DBUser(username=data.username.strip(), password_hash=hashed))
        await session.commit()
    return {"message": "User created successfully"}


@app.post("/login")
async def login(data: UserAuth):
    async with db.async_session() as session:
        res = await session.execute(select(DBUser).where(DBUser.username == data.username.strip()))
        user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=404, detail="User profile footprint absent")
    stored_hash = cast(str, user.password_hash)
    if not crypto.verify_password(data.password, stored_hash):
        raise HTTPException(
            status_code=401, detail="Invalid credential parameters")
    return {"message": f"Welcome {user.username}", "authenticated": True}


@app.get("/")
def root():
    return {"status": "API running", "database_connected": db.engine is not None}


@app.get("/scores")
async def scores():
    try:
        games = await api.fetch_scores()
        if games and isinstance(games, list):
            for g in games:
                if all(k in g for k in ('game_id', 'HomeScore', 'AwayScore')):
                    if g['AwayScore'] > 0 or g['HomeScore'] > 0:
                        await db.settle_predictions(g['game_id'], g['HomeScore'], g['AwayScore'])
        return games
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Internal server error fetching scores")


@app.get("/news")
async def news():
    try:
        results = []
        async for item in api.yield_news():
            if item:
                results.append(item)
        return results
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Internal server error streaming news")


@app.get("/takes")
async def get_takes():
    try:
        takes = await db.get_takes()
        if not takes:
            return []
        ranked = sorted(takes, key=lambda t: t.ranking_score()
                        or 0, reverse=True)
        return [{"id": t.id, "user": t.user, "text": t.text, "votes": t.votes, "downvotes": t.downvotes} for t in ranked]
    except Exception as e:
        return []


@app.post("/takes")
async def add_take(data: HotTakeCreate):
    take = HotTake(user=data.username, text=data.text.strip()[:200])
    await db.save_take(take)
    return {"message": "Hot take broadcast saved"}


@app.post("/vote")
async def vote(data: VoteRequest):
    async with db.async_session() as session:
        res = await session.execute(select(DBHotTake).where(DBHotTake.id == data.id))
        row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=404, detail="Target hot take item row not found")
    current_votes = int(cast(int, row.votes))
    current_downvotes = int(cast(int, row.downvotes))
    if data.type == "up":
        current_votes += 1
    else:
        current_downvotes += 1
    await db.update_votes(data.id, current_votes, current_downvotes)
    return {"message": f"{data.type}vote logged successfully"}


@app.post("/predict")
async def predict(data: PredictionCreate):
    await db.save_prediction(user=data.username, game_id=data.game_id, game_name=data.game_name, home=data.home, away=data.away)
    return {"message": "Game prediction record registered"}


@app.post("/settle")
async def settle(data: SettleRequest):
    await db.settle_predictions(game_id=data.game_id, home=data.home, away=data.away)
    return {"message": "Predictions settled and point margins evaluated"}


@app.get("/performance/{username}")
async def performance(username: str):
    try:
        return await db.get_user_performance(username)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Internal server error parsing stats context")


@app.get("/leaderboard")
async def leaderboard():
    return await db.leaderboard()


@app.get("/items/")
async def read_items(session: AsyncSession = Depends(lambda: nfl_engine.db.async_session())):
    try:
        items = await session.execute(select(DBHotTake))
        return items.scalars().all()
    except Exception as e:
        logging.error(f"Error reading items: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
