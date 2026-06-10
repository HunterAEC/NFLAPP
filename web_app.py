import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from nflapp import NFLApp, HotTake

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global nfl_engine, db, api, crypto
    nfl_engine = NFLApp()
    db = nfl_engine.db
    api = nfl_engine.api
    crypto = nfl_engine.crypto
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
def register(data: UserAuth):
    with db.lock:
        existing = db.conn.execute(
            "SELECT 1 FROM users WHERE username=?", (data.username.strip(),),).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        hashed = crypto.hash_password(data.password)
        db.conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        (data.username.strip(), hashed),)
        db.conn.commit()
    return {"message": "User created successfully"}


@app.post("/login")
def login(data: UserAuth):
    with db.lock:
        user = db.conn.execute(
            "SELECT username, password_hash FROM users WHERE username=?", (data.username.strip(),),).fetchone()
    if not user:
        raise HTTPException(
            status_code=404, detail="User profile footprint absent")
    username, pw_hash = user
    if not crypto.verify_password(data.password, pw_hash):
        raise HTTPException(
            status_code=401, detail="Invalid credential parameters")
    return {"message": f"Welcome {username}", "authenticated": True}


@app.get("/")
def root():
    return {"status": "API running", "database_connected": db.conn is not None}


@app.get("/scores")
async def scores():
    try:
        games = await api.fetch_scores()
        if games and isinstance(games, list):
            for g in games:
                if all(k in g for k in ('game_id', 'HomeScore', 'AwayScore')):
                    try:
                        if g['AwayScore'] > 0 or g['HomeScore'] > 0:
                            db.settle_predictions(
                                g['game_id'], g['HomeScore'], g['AwayScore'])
                    except Exception as db_err:
                        continue
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
def get_takes():
    try:
        takes = db.get_takes()
        if not takes:
            return []
        ranked = sorted(takes, key=lambda t: t.ranking_score()
                        or 0, reverse=True)
        return [{"id": t.id, "user": t.user, "text": t.text, "votes": t.votes, "downvotes": t.downvotes} for t in ranked]
    except Exception as e:
        return []


@app.post("/takes")
def add_take(data: HotTakeCreate):
    take = HotTake(user=data.username, text=data.text.strip()[:200])
    db.save_take(take)
    return {"message": "Hot take broadcast saved"}


@app.post("/vote")
def vote(data: VoteRequest):
    with db.lock:
        row = db.conn.execute(
            "SELECT votes, downvotes FROM hot_takes WHERE id=?", (data.id,)).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail="Target hot take item row not found")
    votes, downs = row
    if data.type == "up":
        votes += 1
    else:
        downs += 1
    db.update_votes(data.id, votes, downs)
    return {"message": f"{data.type}vote logged successfully"}


@app.post("/predict")
def predict(data: PredictionCreate):
    db.save_prediction(user=data.username, game_id=data.game_id,
                       game_name=data.game_name, home=data.home, away=data.away)
    return {"message": "Game prediction record registered"}


@app.post("/settle")
def settle(data: SettleRequest):
    db.settle_predictions(game_id=data.game_id, home=data.home, away=data.away)
    return {"message": "Predictions settled and point margins evaluated"}


@app.get("/performance/{username}")
def performance(username: str):
    try:
        return db.get_user_performance(username)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Internal server error parsing stats context")


@app.get("/leaderboard")
def leaderboard():
    return db.leaderboard()
