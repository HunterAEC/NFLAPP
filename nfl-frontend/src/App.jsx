import { useState } from "react";

function App() {
  const [scores, setScores] = useState([]);
  const [news, setNews] = useState([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [take, setTake] = useState("");
  const [takes, setTakes] = useState([]);

  const API_URL = "http://127.0.0.1:8001";

  // ✅ Load Scores
  const loadScores = async () => {
    const res = await fetch(`${API_URL}/scores`);
    const data = await res.json();
    setScores(data);
  };

  // ✅ Load News
  const loadNews = async () => {
    const res = await fetch(`${API_URL}/news`);
    const data = await res.json();
    setNews(data);
  };

  // ✅ Register
  const register = async () => {
    const res = await fetch(`${API_URL}/users`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });

    const data = await res.json();
    alert(data.message || data.error);
  };

  // ✅ Login
  const login = async () => {
    const res = await fetch(`${API_URL}/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    });

    const data = await res.json();
    alert(data.message || data.error);
  };

  // ✅ Load Takes
  const loadTakes = async () => {
    const res = await fetch(`${API_URL}/takes`);
    const data = await res.json();
    setTakes(data);
  };

  // ✅ Add Take
  const addTake = async () => {
    await fetch(`${API_URL}/takes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, text: take }),
    });

    setTake("");
    loadTakes();
  };

  return (
    <div style={{ padding: "20px", fontFamily: "Arial" }}>
      <h1>🏈 NFL Web App</h1>

      {/* LOGIN */}
      <h2>Login / Register</h2>
      <input
        placeholder="Username"
        onChange={(e) => setUsername(e.target.value)}
      />
      <input
        placeholder="Password"
        type="password"
        onChange={(e) => setPassword(e.target.value)}
      />
      <br />
      <button onClick={login}>Login</button>
      <button onClick={register}>Register</button>

      {/* SCORES */}
      <h2>Scores</h2>
      <button onClick={loadScores}>Load Scores</button>
      <ul>
        {scores.map((g, i) => (
          <li key={i}>
            {g.AwayTeam} {g.AwayScore} - {g.HomeScore} {g.HomeTeam}
          </li>
        ))}
      </ul>

      {/* NEWS */}
      <h2>News</h2>
      <button onClick={loadNews}>Load News</button>
      <ul>
        {news.map((n, i) => (
          <li key={i}>
            <a href={n.link} target="_blank">
              {n.headline}
            </a>
          </li>
        ))}
      </ul>

      {/* HOT TAKES */}
      <h2>Hot Takes</h2>
      <button onClick={loadTakes}>Load Takes</button>

      <ul>
        {takes.map((t, i) => (
          <li key={i}>
            <b>{t.user}</b>: {t.text}
          </li>
        ))}
      </ul>

      <input
        placeholder="Your take..."
        value={take}
        onChange={(e) => setTake(e.target.value)}
      />
      <button onClick={addTake}>Post</button>
    </div>
  );
}

export default App;