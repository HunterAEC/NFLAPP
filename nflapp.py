import os
import sys
import time
import asyncio
import logging
from typing import List, Optional, Callable, Dict, Any, cast
from dataclasses import dataclass, field
import aiohttp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app_config import config
from models import Base, DBUser, DBHotTake, DBPrediction, DBPredictionOutcome

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])

# welcome I new to this and programming in general, let me know what you think.
# Hi Rey, this might suck but I am learning. Hunter Out
# Go Raiders, Mendoza for rookie of the year


class User:
    def __init__(self, db_id: Optional[int], username: str, password_hash: str, score: float = 0.0):
        self.id = db_id
        self.username = username.strip()
        self.password_hash = password_hash
        self.score = score


@dataclass
class HotTake:
    user: str
    text: str
    votes: int = 0
    downvotes: int = 0
    created_at: float = field(default_factory=lambda: time.time())
    id: Optional[int] = None

    def upvote(self):
        self.votes += 1

    def downvote(self):
        self.downvotes += 1

    def ranking_score(self) -> float:
        age_hours = (time.time() - self.created_at) / 3600
        return (self.votes - self.downvotes) / ((age_hours + 2) ** 1.5)


class CryptoManager:
    def __init__(self):
        self.ph = PasswordHasher()

    def hash_password(self, password: str) -> str:
        return self.ph.hash(password)

    def verify_password(self, password: str, hashed: str) -> bool:
        try:
            return self.ph.verify(hashed, password)
        except VerifyMismatchError:
            return False
        except Exception as e:
            logging.error(f"Unexpected cryptographic runtime failure: {e}")
            return False


class API:
    def __init__(self):
        self.session = None

    async def _get_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_scores(self) -> List[dict]:
        try:
            session = await self._get_session()
            url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    games = []
                    for e in data.get("events", []):
                        comp = e["competitions"][0]
                        teams = comp["competitors"]
                        away_team = teams[1] if teams[0]["homeAway"] == "home" else teams[0]
                        home_team = teams[0] if teams[0]["homeAway"] == "home" else teams[1]
                        games.append({
                            "game_id": e["id"],
                            "game_name": e.get("name", "NFL Game"),
                            "AwayTeam": away_team["team"]["abbreviation"],
                            "HomeTeam": home_team["team"]["abbreviation"],
                            "AwayScore": int(away_team.get("score", 0)),
                            "HomeScore": int(home_team.get("score", 0))
                        })
                    return games
        except Exception as e:
            logging.error(f"Error extracting score matrix elements: {e}")
        return []

    async def yield_news(self):
        try:
            session = await self._get_session()
            url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    for a in data.get("articles", []):
                        yield {
                            "headline": a.get("headline"),
                            "link": a.get("links", {}).get("web", {}).get("href"),
                        }
        except Exception as e:
            logging.error(
                f"Async generator pipeline encounter error streaming data models: {e}")


class Database:
    def __init__(self):
        self.engine = create_async_engine(config.DATABASE_URL)
        self.async_session = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession)

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def save_take(self, take: HotTake):
        async with self.async_session() as session:
            db_take = DBHotTake(user=take.user, text=take.text, votes=take.votes,
                                downvotes=take.downvotes, created_at=take.created_at)
            session.add(db_take)
            await session.commit()
            take.id = cast(int, db_take.id)

    async def update_votes(self, take_id: int, votes: int, downvotes: int):
        async with self.async_session() as session:
            await session.execute(update(DBHotTake).where(DBHotTake.id == take_id).values(votes=votes, downvotes=downvotes))
            await session.commit()

    async def get_takes(self) -> List[HotTake]:
        async with self.async_session() as session:
            result = await session.execute(select(DBHotTake))
            rows = result.scalars().all()
        return [HotTake(
            id=cast(Optional[int], r.id),
            user=cast(str, r.user),
            text=cast(str, r.text),
            votes=cast(int, r.votes),
            downvotes=cast(int, r.downvotes),
            created_at=cast(float, r.created_at),
        ) for r in rows]

    async def save_prediction(self, user: str, game_id: str, game_name: str, home: int, away: int):
        async with self.async_session() as session:
            result = await session.execute(select(DBPrediction).where(DBPrediction.user == user, DBPrediction.game_id == game_id, DBPrediction.error == None))
            existing = result.scalar_one_or_none()
            if existing:
                setattr(existing, "pred_home", home)
                setattr(existing, "pred_away", away)
            else:
                session.add(DBPrediction(user=user, game_id=game_id,
                            game_name=game_name, pred_home=home, pred_away=away))
            await session.commit()

    async def settle_predictions(self, game_id: str, home: int, away: int):
        async with self.async_session() as session:
            result = await session.execute(select(DBPrediction).where(DBPrediction.game_id == game_id, DBPrediction.error == None))
            rows = result.scalars().all()
            for r in rows:
                setattr(r, "actual_home", home)
                setattr(r, "actual_away", away)
                # use SQL ABS to operate safely on ColumnElements
                setattr(r, "error", func.abs(r.pred_home - home) +
                        func.abs(r.pred_away - away))
                pred_home = cast(int, r.pred_home)
                pred_away = cast(int, r.pred_away)
                is_correct = 1 if (
                    pred_home == home and pred_away == away) else 0
                session.add(DBPredictionOutcome(username=r.user, game_id=game_id, pred_home=pred_home,
                            pred_away=pred_away, actual_home=home, actual_away=away, is_correct=is_correct))
            await session.commit()

    async def get_user_performance(self, username: str) -> Dict[str, int]:
        async with self.async_session() as session:
            result = await session.execute(select(DBPredictionOutcome.is_correct, func.count(DBPredictionOutcome.id)).where(DBPredictionOutcome.username == username).group_by(DBPredictionOutcome.is_correct))
            results = result.all()
        performance = {'total_predictions': 0,
                       'correct_predictions': 0, 'incorrect_predictions': 0}
        for row in results:
            if row[0] == 1:
                performance['correct_predictions'] += row[1]
            else:
                performance['incorrect_predictions'] += row[1]
            performance['total_predictions'] += row[1]
        return performance

    async def leaderboard(self) -> List[Dict[str, Any]]:
        async with self.async_session() as session:
            result = await session.execute(select(DBPrediction.user, func.count(DBPrediction.id), func.avg(DBPrediction.error)).where(DBPrediction.error != None).group_by(DBPrediction.user).order_by(func.avg(DBPrediction.error).asc()).limit(20))
            rows = result.all()
        return [{"user": r[0], "total": r[1], "avg_error": float(r[2])} for r in rows]


class NFLApp:
    def __init__(self):
        self.api = API()
        self.db = Database()
        self.crypto = CryptoManager()
        self.current_user: Optional[User] = None
        self.hooks: List[Callable[[HotTake], None]] = []

    def register_hook(self, func: Callable[[HotTake], None]):
        self.hooks.append(func)

    async def login(self):
        username = input("Enter username: ").strip()
        if not username:
            return
        password = input("Enter password: ").strip()
        async with self.db.async_session() as session:
            res = await session.execute(select(DBUser).where(DBUser.username == username.strip()))
            db_user = res.scalars().one_or_none()
        if db_user and self.crypto.verify_password(password, cast(str, db_user.password_hash)):
            self.current_user = User(
                db_id=cast(int, db_user.id),
                username=cast(str, db_user.username),
                password_hash=cast(str, db_user.password_hash),
                score=cast(
                    float, db_user.score) if db_user.score is not None else 0.0,
            )
            print(f"\nLogged in safely as: {username}")
        else:
            if not db_user:
                choice = input(
                    "\nUser profile absent. Initialize account credentials row? (y/n): ").strip().lower()
                if choice == 'y':
                    if len(password) < 6:
                        print(
                            "Password size error: Selection length falls short of 6 characters threshold.")
                        return
                    hashed_pw = self.crypto.hash_password(password)
                    async with self.db.async_session() as session:
                        new_db_user = DBUser(
                            username=username, password_hash=hashed_pw)
                        session.add(new_db_user)
                        await session.commit()
                        self.current_user = User(
                            db_id=cast(int, new_db_user.id),
                            username=cast(str, new_db_user.username),
                            password_hash=cast(str, new_db_user.password_hash),
                        )
                    print(f"User matrix registered. Logged in as {username}")
            else:
                print("Invalid authentication parameters.")

    async def show_news(self):
        print("\n=== LATEST NFL NEWS (STREAMED) ===")
        count = 0
        async for article in self.api.yield_news():
            count += 1
            print(f"{count}. {article['headline']}")
            print(f"   Link: {article['link']}")
            if count >= 5:
                break
        if count == 0:
            print(
                "No real-time articles could be streamed from provider API context channels.")

    async def show_scores(self):
        games = await self.api.fetch_scores()
        if not games:
            print("Unable to collect scoreboard parameters from network channel.")
            return
        print("\n=== CURRENT SCOREBOARD ===")
        for i, g in enumerate(games, 1):
            print(f"[{i}] {g['game_name']}")
            print(
                f"    {g['AwayTeam']} {g['AwayScore']} @ {g['HomeTeam']} {g['HomeScore']}")
            print("-" * 35)
        return games

    async def make_prediction(self):
        if not self.current_user:
            print(
                "Authentication routing blocked: Login required to construct predictions.")
            return
        games = await self.show_scores()
        if not games:
            return
        try:
            choice = int(
                input("\nSelect game line number identifier to predict: ")) - 1
            if 0 <= choice < len(games):
                target = games[choice]
                pred_away = int(
                    input(f"Enter predicted score for {target['AwayTeam']}: "))
                pred_home = int(
                    input(f"Enter predicted score for {target['HomeTeam']}: "))
                await self.db.save_prediction(user=self.current_user.username, game_id=target['game_id'], game_name=target['game_name'], home=pred_home, away=pred_away)
                print("Game prediction records logged successfully.")
            else:
                print("Selection context out of bounds.")
        except ValueError:
            print("Numeric processing exception triggered. Action aborted.")

    async def evaluate_live_scores(self):
        print("\nSynchronizing engine metrics against remote scoreboard updates...")
        games = await self.api.fetch_scores()
        if not games:
            print("Sync cycle skipped.")
            return
        for g in games:
            if g['AwayScore'] > 0 or g['HomeScore'] > 0:
                await self.db.settle_predictions(g['game_id'], g['HomeScore'], g['AwayScore'])
        print("Data updates verified.")

    async def show_prediction_leaderboard(self):
        leaders = await self.db.leaderboard()
        print("\n=== ACCURACY LEADERBOARD (LOWER ERROR IS BETTER) ===")
        if not leaders:
            print("No settled prediction records parsed in the current system timeline.")
            return
        for i, row in enumerate(leaders, 1):
            print(
                f"{i}. User: {row['user']} | Settled Games: {row['total']} | Avg Error Point Margin: {row['avg_error']:.2f}")

    async def display_current_user_performance(self):
        if not self.current_user:
            print(
                "Authentication routing blocked: Login required to review profile performance.")
            return
        perf = await self.db.get_user_performance(self.current_user.username)
        print(f"\nUser: {self.current_user.username}")
        print(f"Total Predictions: {perf['total_predictions']}")
        if perf['total_predictions'] > 0:
            print(
                f"Correct Predictions: {perf['correct_predictions']} ({perf['correct_predictions'] / perf['total_predictions'] * 100:.2f}%)")
            print(
                f"Incorrect Predictions: {perf['incorrect_predictions']} ({perf['incorrect_predictions'] / perf['total_predictions'] * 100:.2f}%)")
        else:
            print("Correct Predictions: 0 (0.00%)")
            print("Incorrect Predictions: 0 (0.00%)")

    async def add_take(self):
        if not self.current_user:
            print("Login required to proceed with this task selection choice.")
            return
        text = input("Enter take text content: ").strip()
        if not text:
            return
        take = HotTake(user=self.current_user.username, text=text)
        for hook in self.hooks:
            hook(take)
        await self.db.save_take(take)
        print("Hot take broadcast verified.")

    async def show_takes(self) -> List[HotTake]:
        print("\n=== HOT TAKES RANKED BY TIME-DECAY POPULARITY ===")
        takes = await self.db.get_takes()
        ranked = sorted(takes, key=lambda t: t.ranking_score(), reverse=True)
        if not ranked:
            print(
                "No active hot takes tracking inside database instance storage objects.")
        for i, t in enumerate(ranked, 1):
            print(
                f"{i}. [{t.ranking_score():.2f}] {t.user}: {t.text} (Votes: +{t.votes}/-{t.downvotes})")
        return ranked

    async def vote_take_interface(self, direction: str):
        ranked = await self.show_takes()
        if not ranked:
            return
        try:
            choice = int(
                input(f"\nSelect take index to {direction}vote: ")) - 1
            if 0 <= choice < len(ranked):
                target = ranked[choice]
                if direction == "up":
                    target.upvote()
                else:
                    target.downvote()
                if target.id is None:
                    print("Unable to update vote record: invalid take identifier.")
                    return
                await self.db.update_votes(target.id, target.votes, target.downvotes)
                print(
                    f"{direction.capitalize()}vote logged matrix records update complete.")
            else:
                print("Selection index out of range bounds array template.")
        except ValueError:
            print("Numeric validation expected.")

    async def menu(self):
        await self.db.create_tables()
        while True:
            profile_tag = self.current_user.username if self.current_user else "Unauthenticated Guest"
            print(f"\n================ NFL CORE APP ENGINE ================")
            print(f" Current Session Context: {profile_tag}")
            print("-----------------------------------------------------")
            print("1. Login / Create Account")
            print("2. Show Streaming News")
            print("3. Show Live Scores & Sync Predictions")
            print("4. Submit Score Prediction Model")
            print("5. View Prediction Leaderboard Accuracy")
            print("6. View My Prediction Stats")
            print("7. Show Ranked Hot Takes")
            print("8. Add Custom Take Row")
            print("9. Upvote Hot Take")
            print("10. Downvote Hot Take")
            print("0. Exit Engine Pipeline")
            choice = input("\nEnter choice (0-10): ").strip()
            if choice == "1":
                await self.login()
            elif choice == "2":
                await self.show_news()
            elif choice == "3":
                await self.show_scores()
                await self.evaluate_live_scores()
            elif choice == "4":
                await self.make_prediction()
            elif choice == "5":
                await self.show_prediction_leaderboard()
            elif choice == "6":
                await self.display_current_user_performance()
            elif choice == "7":
                await self.show_takes()
            elif choice == "8":
                await self.add_take()
            elif choice == "9":
                await self.vote_take_interface("up")
            elif choice == "10":
                await self.vote_take_interface("down")
            elif choice == "0":
                print("Closing processing infrastructure safely. Goodbye.")
                await self.api.close()
                break
            else:
                print("Invalid choice configuration option mapping template.")


async def main():
    app = NFLApp()
    app.register_hook(lambda t: setattr(t, "text", t.text.strip()[:200]))
    await app.menu()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
