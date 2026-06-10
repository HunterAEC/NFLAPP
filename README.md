# 🏈 NFL Core App & Prediction Engine

Welcome to the **NFL Core App & Prediction Engine**, a centralized hub designed specifically for football fans who want real-time sports updates, straightforward content delivery, and a competitive platform to test their football intuition. 

Unlike mainstream social sports networks, this application filters out heavy, tracking layers—offering a pure, data-backed experience driven entirely by raw user engagement.

---

## 🚀 Key Features

### 🧼 Transparent, Algorithm-Free Hot Takes
* **Community-Driven Feeds:** Post your boldest NFL statements and watch them rise or fall organically.
* **Organic Time-Decay Ranking:** Content priority relies on a mathematical time-decay formula based entirely on real user upvotes and downvotes. There are no hidden recommendation metrics or corporately promoted discovery tracks.

### 📰 Unified Streaming News Hub
* **All-In-One Dashboard:** Streams real-time articles and major headlines directly from live sports provider interfaces. 
* **Zero Noise:** Access immediate breaking updates from across the league directly in a single, clean interface without looking through promotional fillers.

### 📊 Score Prediction & Accuracy Analytics
* **Predict Live Matchups:** Submit score forecasts for upcoming or active matchups featured on the live scoreboard.
* **Automatic Game Settling:** The processing background tracks live score indicators and evaluates predictions as match rows update.
* **Granular Performance Statistics:** Track your skill over the season with automated performance telemetry detailing total predictions, precise correct/incorrect splits, and precision percentage ratings.
* **Global Accuracy Leaderboard:** Climb a public leaderboard where users are ranked using an absolute error point-margin metric (lower average point variance yields a higher rank).

---

## 🛠️ Tech Stack & Architecture

The application uses an asynchronous infrastructure optimized to handle multiple concurrent tracking pipelines smoothly:

* **Backend Framework:** FastAPI (Python 3.13)
* **Asynchronous Database Layer:** SQLAlchemy 2.0 with the `aiosqlite` async driver (SQLite backend)
* **Data Ingestion:** `aiohttp` for non-blocking external score scoreboard fetching
* **Cryptographic Security:** Argon2 (`argon2-cffi`) for secure, salted password hashing

---

## 📂 Project Directory Structure

```text
NFLAPP/
├── .gitignore              # Filters database files, caches, and environment system files
├── LICENSE                 # Public open-source permissions file (MIT)
├── README.md               # Main repository guide and technical architecture overview
├── requirements.txt        # Master pip installation dependencies manifest
├── app_config.py           # Database URL locations and decoupled app state configurations
├── models.py               # Declarative async SQLAlchemy relational data tables
├── nflapp.py               # Application engine pipelines and terminal CLI interactive menu
└── web_app.py              # FastAPI application server routing rules and API schema contexts
