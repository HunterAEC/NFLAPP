import time
from sqlalchemy import Column, Integer, String, Float
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    score = Column(Float, default=0.0)


class DBHotTake(Base):
    __tablename__ = "hot_takes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String)
    text = Column(String)
    votes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)
    created_at = Column(Float, default=lambda: time.time())


class DBPrediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String)
    game_id = Column(String, index=True)
    game_name = Column(String)
    pred_home = Column(Integer)
    pred_away = Column(Integer)
    actual_home = Column(Integer, default=None)
    actual_away = Column(Integer, default=None)
    error = Column(Integer, default=None)


class DBPredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, index=True)
    game_id = Column(String)
    pred_home = Column(Integer)
    pred_away = Column(Integer)
    actual_home = Column(Integer)
    actual_away = Column(Integer)
    is_correct = Column(Integer)
