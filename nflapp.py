import os
import sys
import time
import asyncio
import logging
import sqlite3
from datetime import datetime
from typing import List, Optional, Callable, Dict, Any, Tuple
from dataclasses import dataclass, field
import aiohttp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Configure professional diagnostic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Environment and Configuration Management
NFL_API_KEY = os.getenv("NFL_API_KEY", "")
DB_NAME = os.getenv("NFL_DB_NAME", "nfl_app.db")
CACHE_TTL_SECONDS = 300  # 5 minutes caching window
MAX_RETRIES = 3


class User:
    """Integrated Active Record style data representation layer."""

    def __init__(self, db_id: Optional[int], username: str, password_hash: str, favorite_team: Optional[str] = None, score: float = 0.0, is_admin: bool = False):
        self.id = db_id
        self.username = username.strip()
        self.password_hash = password_hash
        self.favorite_team = favorite_team.upper() if favorite_team else None
        self.score = score
        self.is_admin = is_admin

    def set_favorite(self, team: str):
        self.favorite_team = team.upper() if team.strip() else None

    def update_score(self):
        self.score += 1.0

    def save(self, conn: sqlite3.Connection):
        """Saves or updates state metrics safely using transaction cursors."""
        cursor = conn.cursor()
        if self.id is None:
            cursor.execute(
                "INSERT INTO users (username, password_hash, favorite_team, score, is_admin) VALUES (?, ?, ?, ?, ?)",
                (self.username, self.password_hash, self.favorite_team,
                 self.score, 1 if self.is_admin else 0)
            )
            self.id = cursor.lastrowid
        else:
            cursor.execute(
                "UPDATE users SET username=?, password_hash=?, favorite_team=?, score=?, is_admin=? WHERE id=?",
                (self.username, self.password_hash, self.favorite_team,
                 self.score, 1 if self.is_admin else 0, self.id)
            )
        conn.commit()

    @staticmethod
    def get_user(username: str, conn: sqlite3.Connection) -> Optional['User']:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, favorite_team, score, is_admin FROM users WHERE username=?", (username.strip(),))
        user_data = cursor.fetchone()
        if user_data:
            return User(
                db_id=user_data[0],
                username=user_data[1],
                password_hash=user_data[2],
                favorite_team=user_data[3],
                score=user_data[4],
                is_admin=bool(user_data[5])
            )
        return None


@dataclass
class HotTake:
    user: str
    text: str
    votes: int = 0
    downvotes: int = 0
    created_at: float = field(
        default_factory=lambda: datetime.now().timestamp())
    id: Optional[int] = None

    def upvote(self):
        self.votes += 1

    def downvote(self):
        self.downvotes += 1

    def score(self) -> float:
        """Dynamic time-decay popularity formula logic."""
        age_hours = (datetime.now().timestamp() - self.created_at) / 3600
        return (self.votes - self.downvotes) / ((age_hours + 2) ** 1.5)


class CryptoManager:
    """Handles secure cryptographic parameters natively utilizing Argon2id."""

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
    """Asynchronous pipeline client optimized with an in-memory caching mechanism."""

    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = {}

    def _get_cached_data(self, key: str) -> Optional[Any]:
        if key in self.cache:
            entry = self.cache[key]
            if time.time() - entry["timestamp"] < CACHE_TTL_SECONDS:
                return entry["data"]
        return None

    def _set_cache_data(self, key: str, data: Any):
        self.cache[key] = {"data": data, "timestamp": time.time()}

    async def _fetch_with_backoff(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status in [429, 500, 502, 503, 504]:
                            logging.warning(
                                f"Transient HTTP {response.status} code warning on attempt {attempt}")
                        else:
                            logging.error(
                                f"Fatal HTTP handling exception error encountered: {response.status}")
                            return None
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logging.warning(
                        f"Network request timeout on retry cycle iteration {attempt}: {e}")

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
            return None

    async def fetch_news(self) -> List[dict]:
        cached = self._get_cached_data("news")
        if cached:
            return cached

        url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
        raw_data = await self._fetch_with_backoff(url)
        if not raw_data:
            return []

        try:
            normalized_articles = [
                {
                    "headline": item.get("headline", ""),
                    "link": item.get("links", {}).get("web", {}).get("href", "")
                }
                for item in raw_data.get("articles", [])
            ]
            self._set_cache_data("news", normalized_articles)
            return normalized_articles
        except Exception as e:
            logging.error(
                f"Error structuring raw JSON values data models mapping: {e}")
            return []

    async def fetch_scores(self) -> List[dict]:
        if not NFL_API_KEY:
            logging.warning(
                "Scores query bypassed: Missing NFL_API_KEY tokens.")
            return []

        cached = self._get_cached_data("scores")
        if cached:
            return cached

        url = "https://api.sportsdata.io/v3/nfl/scores/json/ScoresByWeek/2025REG/1"
        headers = {"Ocp-Apim-Subscription-Key": NFL_API_KEY}

        raw_scores = await self._fetch_with_backoff(url, headers=headers)
        if raw_scores:
            self._set_cache_data("scores", raw_scores)
            return raw_scores
        return []


class Database:
    """Manages explicit parameter serialization loops and data table initialization."""

    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME)
        self.create_tables()
        self._seed_admin_account()

    def create_tables(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    favorite_team TEXT,
                    score REAL DEFAULT 0.0,
                    is_admin INTEGER DEFAULT 0
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS hot_takes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    text TEXT NOT NULL,
                    votes INTEGER DEFAULT 0,
                    downvotes INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)

    def _seed_admin_account(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM users WHERE username = 'admin'")
            if not cursor.fetchone():
                hashed = CryptoManager().hash_password("admin123")
                # Using INSERT OR IGNORE safely prevents collision crashes
                self.conn.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash, favorite_team, score, is_admin) VALUES (?, ?, ?, ?, ?)",
                    ("admin", hashed, "NFL", 0.0, 1)
                )
                self.conn.commit()
                logging.info(
                    "Platform administrative baseline user verified/seeded.")
        except sqlite3.OperationalError as e:
            logging.error(
                f"Schema mismatch detected: {e}. Dropping the old nfl_app.db file is required to rebuild table columns.")

    def save_hot_take(self, take: HotTake):
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO hot_takes (user, text, votes, downvotes, created_at) VALUES (?, ?, ?, ?, ?)",
                    (take.user, take.text, take.votes,
                     take.downvotes, take.created_at)
                )
                take.id = cursor.lastrowid
        except sqlite3.Error as e:
            logging.error(
                f"Database insertion exception writing hot take structure row: {e}")

    def update_take_votes(self, take: HotTake):
        if take.id is None:
            return
        try:
            with self.conn:
                self.conn.execute(
                    "UPDATE hot_takes SET votes=?, downvotes=? WHERE id=?", (take.votes, take.downvotes, take.id))
        except sqlite3.Error as e:
            logging.error(
                f"Database runtime failure updating interaction scores table: {e}")

    def load_all_takes(self) -> List[HotTake]:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT user, text, votes, downvotes, created_at, id FROM hot_takes")
            return [HotTake(user=r[0], text=r[1], votes=r[2], downvotes=r[3], created_at=r[4], id=r[5]) for r in cursor.fetchall()]
        except sqlite3.Error as e:
            logging.error(
                f"Database error loading data profiles context rows: {e}")
            return []

    def clear_all_metrics(self):
        try:
            with self.conn:
                self.conn.execute("UPDATE users SET score = 0.0")
                self.conn.execute("DELETE FROM hot_takes")
            logging.info("Platform tracking entities tables reset completed.")
        except sqlite3.Error as e:
            logging.error(
                f"Database failure executing table truncate optimization matrices: {e}")


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

        # Verify using our secure cryptographic interface layer
        if user and self.crypto.verify_password(password, user.password_hash):
            self.current_user = user
            print(f"Logged in as {username}")
        else:
            if not user:
                choice = input(
                    "User footprint absent. Initialize account credentials row? (y/n): ").strip().lower()
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
                    print(f"User created. Logged in as {username}")
            else:
                print("Invalid credentials")

    async def show_news(self):
        news = await self.api.fetch_news()
        if not news:
            print("Unable to collect remote news entries from provider.")
            return

        if self.current_user and self.current_user.favorite_team:
            fav = self.current_user.favorite_team.lower()
            news.sort(key=lambda x: fav in x["headline"].lower(), reverse=True)

        print("\nNEWS")
        for i, item in enumerate(news[:5], 1):
            print(f"{i}. {item['headline']}")
            print(f"   Link: {item['link']}")

    async def show_scores(self):
        scores = await self.api.fetch_scores()
        if not scores:
            print("Unable to collect scoreboard parameters from network channel.")
            return

        print("\nSCORES")
        for g in scores[:5]:
            print(f"{g.get('AwayTeam', 'UNK')} @ {g.get('HomeTeam', 'UNK')}")
            print(f"{g.get('AwayScore', 0)} - {g.get('HomeScore', 0)}")
            print("-" * 25)

    async def add_take(self):
        if not self.current_user:
            print("Login required to proceed with this task selection choice.")
            return
        text = input("Enter take: ").strip()
        if not text:
            return

        take = HotTake(user=self.current_user.username, text=text)
        for hook in self.hooks:
            hook(take)

        self.db.save_hot_take(take)
        self.current_user.update_score()
        self.current_user.save(self.db.conn)
        print("Hot take broadcast verified.")

    def show_takes(self) -> List[HotTake]:
        print("\nHOT TAKES")
        takes = self.db.load_all_takes()
        ranked = sorted(takes, key=lambda t: t.score(), reverse=True)

        if not ranked:
            print("No hot takes present in system timeline.")
        for i, t in enumerate(ranked, 1):
            print(
                f"{i}. [{t.score():.2f}] {t.user}: {t.text} (Votes: +{t.votes}/-{t.downvotes})")
        return ranked

    async def upvote_take(self):
        ranked = self.show_takes()
        if not ranked:
            return
        try:
            choice = int(
                input("\nSelect take line identifier to upvote: ")) - 1
            if 0 <= choice < len(ranked):
                target = ranked[choice]
                target.upvote()
                self.db.update_take_votes(target)
                print("Upvote logged.")
            else:
                print("Selection context out of bounds index array.")
        except ValueError:
            print("Numeric selector parsing expected.")

    async def downvote_take(self):
        ranked = self.show_takes()
        if not ranked:
            return
        try:
            choice = int(
                input("\nSelect take line identifier to downvote: ")) - 1
            if 0 <= choice < len(ranked):
                target = ranked[choice]
                target.downvote()
                self.db.update_take_votes(target)
                print("Downvote logged.")
            else:
                print("Selection context out of bounds index array.")
        except ValueError:
            print("Numeric selector parsing expected.")

    async def set_favorite_team(self):
        if not self.current_user:
            print("Login required to proceed with this task selection choice.")
            return
        team = input("Enter team tracking tag abbreviation: ").strip()
        if team:
            self.current_user.set_favorite(team)
            self.current_user.save(self.db.conn)
            print(
                f"Personal filter criteria prioritized: {self.current_user.favorite_team}")

    async def administrative_purge_routine(self):
        if self.current_user and self.current_user.is_admin:
            confirm = input(
                "Confirm destruction of platform metrics? (y/n): ").strip().lower()
            if confirm == 'y':
                self.db.clear_all_metrics()
                # Update current active user session instance in-memory to match database state
                self.current_user.score = 0.0
                print("Standings data models cleared.")
        else:
            print(
                "Access Restriction Notice: Administrative parameters verification error.")

    async def menu(self):
        while True:
            profile_tag = self.current_user.username if self.current_user else "Unauthenticated Guest"
            admin_extension = " [SYS ADMIN]" if (
                self.current_user and self.current_user.is_admin) else ""

            print(f"\n================ NFL CORE APP ENGINE ================")
            print(f" Current Session Context: {profile_tag}{admin_extension}")
            print("-----------------------------------------------------")
            print("1. Login")
            print("2. Show News")
            print("3. Show Scores")
            print("4. Show Hot Takes")
            print("5. Add Custom Take")
            print("6. Upvote Target Take")
            print("7. Downvote Target Take")
            print("8. Configure Favorite Team Filter")
            print("9. Exit")
            if self.current_user and self.current_user.is_admin:
                print("0. Administrative System Data Purge Routine")

            choice = input("\nEnter choice (0-9): ").strip()

            if choice == "1":
                await self.login()
            elif choice == "2":
                await self.show_news()
            elif choice == "3":
                await self.show_scores()
            elif choice == "4":
                self.show_takes()
            elif choice == "5":
                await self.add_take()
            elif choice == "6":
                await self.upvote_take()
            elif choice == "7":
                await self.downvote_take()
            elif choice == "8":
                await self.set_favorite_team()
            elif choice == "0":
                await self.administrative_purge_routine()
            elif choice == "9":
                print("Exiting the application framework pipeline safely.")
                break
            else:
                print("Invalid choice configuration option mapping template.")


async def main():
    app = NFLApp()
    # Apply input formatting hook logic limiting raw character sets inputs sizing bounds
    app.register_hook(lambda t: setattr(t, "text", t.text.strip()[:200]))
    await app.menu()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
