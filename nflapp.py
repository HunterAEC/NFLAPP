import os
import sys
from datetime import datetime
from typing import List, Optional, Callable
import requests
import logging
from dataclasses import dataclass, field

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Environment variable for NFL API key
NFL_API_KEY = os.getenv("NFL_API_KEY", "")


@dataclass
class User:
    username: str
    favorite_team: Optional[str] = None

    def set_favorite(self, team: str):
        self.favorite_team = team.upper()


@dataclass
class HotTake:
    user: str
    text: str
    votes: int = 0
    downvotes: int = 0
    created_at: float = field(
        default_factory=lambda: datetime.now().timestamp())

    def upvote(self):
        self.votes += 1

    def downvote(self):
        self.downvotes += 1

    def score(self) -> float:
        age_hours = (datetime.now().timestamp() - self.created_at) / 3600
        return (self.votes - self.downvotes) / ((age_hours + 2) ** 1.5)


class NFLAPI:
    def __init__(self):
        self.session = requests.Session()
        self.api_key = NFL_API_KEY

    def fetch_news(self) -> List[dict]:
        try:
            response = self.session.get(
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news",
                timeout=5
            )
            response.raise_for_status()

            data = response.json()
            return [
                {
                    "headline": item.get("headline", ""),
                    "link": item.get("links", {}).get("web", {}).get("href", "")
                }
                for item in data.get("articles", [])
            ]
        except requests.RequestException as e:
            logging.error(f"News fetch failed: {e}")
            return []

    def fetch_scores(self) -> List[dict]:
        if not self.api_key:
            return []

        try:
            url = "https://api.sportsdata.io/v3/nfl/scores/json/ScoresByWeek/2025REG/1"
            headers = {"Ocp-Apim-Subscription-Key": self.api_key}

            response = self.session.get(url, headers=headers, timeout=5)
            response.raise_for_status()

            return response.json()
        except requests.RequestException as e:
            logging.error(f"Scores fetch failed: {e}")
            return []


class NFLApp:
    def __init__(self):
        self.api = NFLAPI()
        self.users = {}
        self.current_user: Optional[User] = None
        self.hot_takes: List[HotTake] = []
        self.hooks: List[Callable[[HotTake], None]] = []

    def register_hook(self, func: Callable[[HotTake], None]):
        self.hooks.append(func)

    def login(self):
        name = input("Username: ").strip()

        if not name:
            return

        if name not in self.users:
            self.users[name] = User(name)
            print("User created")

        self.current_user = self.users[name]
        print(f"Welcome {name}")

    def show_news(self):
        news = self.api.fetch_news()

        if self.current_user and self.current_user.favorite_team:
            fav = self.current_user.favorite_team.lower()
            news.sort(key=lambda x: fav in x["headline"].lower(), reverse=True)

        print("\nNEWS")

        for i, item in enumerate(news[:5], 1):
            print(f"{i}. {item['headline']}")
            print(item["link"])

    def show_scores(self):
        scores = self.api.fetch_scores()

        print("\nSCORES")

        for g in scores[:5]:
            print(f"{g.get('AwayTeam')} @ {g.get('HomeTeam')}")
            print(f"{g.get('AwayScore')} - {g.get('HomeScore')}")

    def add_take(self):
        if not self.current_user:
            print("Login required")
            return

        text = input("Enter take: ").strip()

        if not text:
            return

        take = HotTake(user=self.current_user.username, text=text)

        for hook in self.hooks:
            hook(take)

        self.hot_takes.append(take)

    def show_takes(self):
        print("\nHOT TAKES")

        ranked = sorted(self.hot_takes, key=lambda t: t.score(), reverse=True)

        for t in ranked:
            print(f"[{t.score():.2f}] {t.user}: {t.text}")

    def upvote_take(self):
        self.show_takes()

        try:
            i = int(input("Pick take to upvote: ")) - 1
            if 0 <= i < len(self.hot_takes):
                self.hot_takes[i].upvote()
                print(f"Upvoted: {self.hot_takes[i].text}")
            else:
                print("Invalid choice")
        except ValueError:
            print("Invalid input. Please enter a number.")

    def downvote_take(self):
        self.show_takes()

        try:
            i = int(input("Pick take to downvote: ")) - 1
            if 0 <= i < len(self.hot_takes):
                self.hot_takes[i].downvote()
                print(f"Downvoted: {self.hot_takes[i].text}")
            else:
                print("Invalid choice")
        except ValueError:
            print("Invalid input. Please enter a number.")

    def set_favorite(self):
        if not self.current_user:
            print("Login required")
            return

        team = input("Team: ").strip()

        if team:
            self.current_user.set_favorite(team)

    def menu(self):
        while True:
            print("\nMENU")
            print("1. News")
            print("2. Scores")
            print("3. Hot Takes")
            print("4. Add Take")
            print("5. Upvote Take")
            print("6. Downvote Take")
            print("7. Login")
            print("8. Set Favorite Team")
            print("9. Exit")

            choice = input("> ").strip()

            if choice == "1":
                self.show_news()
            elif choice == "2":
                self.show_scores()
            elif choice == "3":
                self.show_takes()
            elif choice == "4":
                self.add_take()
            elif choice == "5":
                self.upvote_take()
            elif choice == "6":
                self.downvote_take()
            elif choice == "7":
                self.login()
            elif choice == "8":
                self.set_favorite()
            elif choice == "9":
                break
            else:
                print("Invalid choice. Please try again.")


if __name__ == "__main__":
    app = NFLApp()

    # Example plugin: limit length
    app.register_hook(lambda t: setattr(t, "text", t.text[:200]))

    app.menu()
