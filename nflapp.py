import os
import sys
import time
import asyncio
import logging
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional, Callable, Dict, Any, Tuple
from dataclasses import dataclass, field
import aiohttp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
DB_NAME = os.getenv("NFL_DB_NAME", "nfl_app.db")


class User:
    def __init__(self, db_id: Optional[int], username: str, password_hash: str, score: float = 0.0):
        self.id = db_id
        self.username = username.strip()
        self.password_hash = password_hash
        self.score = score

    @staticmethod
    def get_user(username: str, conn: sqlite3.Connection) -> Optional['User']:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, score FROM users WHERE username=?", (username.strip(),))
        user_data = cursor.fetchone()
        if user_data:
            return User(db_id=user_data[0], username=user_data[1], password_hash=user_data[2], score=user_data[3])
        return None

    def save(self, conn: sqlite3.Connection):
        cursor = conn.cursor()
        if self.id is None:
            cursor.execute("INSERT INTO users (username, password_hash, score) VALUES (?, ?, ?)",
                           (self.username, self.password_hash, self.score))
            self.id = cursor.lastrowid
        else:
            cursor.execute("UPDATE users SET username=?, password_hash=?, score=? WHERE id=?",
                           (self.username, self.password_hash, self.score, self.id))
        conn.commit()


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
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.lock = threading.Lock()
        self.create_tables()

    def create_tables(self):
        with self.lock:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE,
                        password_hash TEXT,
                        score REAL DEFAULT 0
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS hot_takes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user TEXT,
                        text TEXT,
                        votes INTEGER,
                        downvotes INTEGER,
                        created_at REAL
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS predictions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user TEXT,
                        game_id TEXT,
                        game_name TEXT,
                        pred_home INTEGER,
                        pred_away INTEGER,
                        actual_home INTEGER DEFAULT NULL,
                        actual_away INTEGER DEFAULT NULL,
                        error INTEGER DEFAULT NULL
                    )
                """)
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS prediction_outcomes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        game_id TEXT,
                        pred_home INTEGER,
                        pred_away INTEGER,
                        actual_home INTEGER,
                        actual_away INTEGER,
                        is_correct INTEGER
                    )
                """)

    def save_take(self, take: HotTake):
        with self.lock:
            with self.conn:
                cur = self.conn.execute("INSERT INTO hot_takes (user, text, votes, downvotes, created_at) VALUES (?, ?, ?, ?, ?)", (
                    take.user, take.text, take.votes, take.downvotes, take.created_at))
                take.id = cur.lastrowid

    def update_votes(self, take_id: int, votes: int, downvotes: int):
        with self.lock:
            with self.conn:
                self.conn.execute(
                    "UPDATE hot_takes SET votes=?, downvotes=? WHERE id=?", (votes, downvotes, take_id))

    def get_takes(self) -> List[HotTake]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, user, text, votes, downvotes, created_at FROM hot_takes").fetchall()
        return [HotTake(id=r[0], user=r[1], text=r[2], votes=r[3], downvotes=r[4], created_at=r[5]) for r in rows]

    def save_prediction(self, user: str, game_id: str, game_name: str, home: int, away: int):
        with self.lock:
            with self.conn:
                cur = self.conn.cursor()
                cur.execute(
                    "SELECT id FROM predictions WHERE user=? AND game_id=? AND error IS NULL", (user, game_id))
                existing = cur.fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE predictions SET pred_home=?, pred_away=? WHERE id=?", (home, away, existing[0]))
                else:
                    self.conn.execute("INSERT INTO predictions (user, game_id, game_name, pred_home, pred_away) VALUES (?, ?, ?, ?, ?)", (
                        user, game_id, game_name, home, away))

    def settle_predictions(self, game_id: str, home: int, away: int):
        with self.lock:
            with self.conn:
                rows = self.conn.execute(
                    "SELECT id, pred_home, pred_away, user FROM predictions WHERE game_id=? AND error IS NULL", (game_id,)).fetchall()
                for r in rows:
                    pid, ph, pa, username = r
                    error = abs(ph - home) + abs(pa - away)
                    self.conn.execute(
                        "UPDATE predictions SET actual_home=?, actual_away=?, error=? WHERE id=?", (home, away, error, pid))
                    is_correct = 1 if (ph == home and pa == away) else 0
                    self.conn.execute("INSERT INTO prediction_outcomes (username, game_id, pred_home, pred_away, actual_home, actual_away, is_correct) VALUES (?, ?, ?, ?, ?, ?, ?)", (
                        username, game_id, ph, pa, home, away, is_correct))

    def get_user_performance(self, username: str) -> Dict[str, int]:
        with self.lock:
            results = self.conn.execute(
                "SELECT is_correct, COUNT(*) FROM prediction_outcomes WHERE username = ? GROUP BY is_correct", (username,)).fetchall()
        performance = {'total_predictions': 0,
                       'correct_predictions': 0, 'incorrect_predictions': 0}
        for row in results:
            if row[0] == 1:
                performance['correct_predictions'] += row[1]
            else:
                performance['incorrect_predictions'] += row[1]
            performance['total_predictions'] += row[1]
        return performance

    def leaderboard(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT user, COUNT(*), AVG(error) FROM predictions WHERE error IS NOT NULL GROUP BY user ORDER BY AVG(error) ASC LIMIT 20").fetchall()
        return [{"user": r[0], "total": r[1], "avg_error": r[2]} for r in rows]


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
        user = User.get_user(username, self.db.conn)
        if user and self.crypto.verify_password(password, user.password_hash):
            self.current_user = user
            print(f"\nLogged in safely as: {username}")
        else:
            if not user:
                choice = input(
                    "\nUser profile absent. Initialize account credentials row? (y/n): ").strip().lower()
                if choice == 'y':
                    if len(password) < 6:
                        print(
                            "Password size error: Selection length falls short of 6 characters threshold.")
                        return
                    hashed_pw = self.crypto.hash_password(password)
                    new_user = User(db_id=None, username=username,
                                    password_hash=hashed_pw)
                    new_user.save(self.db.conn)
                    self.current_user = new_user
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
                self.db.save_prediction(
                    user=self.current_user.username, game_id=target['game_id'], game_name=target['game_name'], home=pred_home, away=pred_away)
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
                self.db.settle_predictions(
                    g['game_id'], g['HomeScore'], g['AwayScore'])
        print("Data updates verified.")

    def show_prediction_leaderboard(self):
        leaders = self.db.leaderboard()
        print("\n=== ACCURACY LEADERBOARD (LOWER ERROR IS BETTER) ===")
        if not leaders:
            print("No settled prediction records parsed in the current system timeline.")
            return
        for i, row in enumerate(leaders, 1):
            print(
                f"{i}. User: {row['user']} | Settled Games: {row['total']} | Avg Error Point Margin: {row['avg_error']:.2f}")

    def display_current_user_performance(self):
        if not self.current_user:
            print(
                "Authentication routing blocked: Login required to review profile performance.")
            return
        perf = self.db.get_user_performance(self.current_user.username)
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
        self.db.save_take(take)
        print("Hot take broadcast verified.")

    def show_takes(self) -> List[HotTake]:
        print("\n=== HOT TAKES RANKED BY TIME-DECAY POPULARITY ===")
        takes = self.db.get_takes()
        ranked = sorted(takes, key=lambda t: t.ranking_score(), reverse=True)
        if not ranked:
            print(
                "No active hot takes tracking inside database instance storage objects.")
        for i, t in enumerate(ranked, 1):
            print(
                f"{i}. [{t.ranking_score():.2f}] {t.user}: {t.text} (Votes: +{t.votes}/-{t.downvotes})")
        return ranked

    async def vote_take_interface(self, direction: str):
        ranked = self.show_takes()
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
                self.db.update_votes(target.id, target.votes, target.downvotes)
                print(
                    f"{direction.capitalize()}vote logged matrix records update complete.")
            else:
                print("Selection index out of range bounds array template.")
        except ValueError:
            print("Numeric validation expected.")

    async def menu(self):
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
                self.show_prediction_leaderboard()
            elif choice == "6":
                self.display_current_user_performance()
            elif choice == "7":
                self.show_takes()
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


def create_components():
    engine = NFLApp()
    return engine.db, engine.api, engine.crypto
