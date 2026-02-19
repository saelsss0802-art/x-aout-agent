"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export default function AgentControls({ agentId }) {
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function stopAgent(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    const payload = { reason };
    if (until) payload.until = new Date(until).toISOString();
    const res = await fetch(`${API_BASE}/api/agents/${agentId}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      setError("Stop request failed");
      return;
    }
    setMessage("Stopped successfully");
  }

  async function resumeAgent() {
    setError("");
    setMessage("");
    const res = await fetch(`${API_BASE}/api/agents/${agentId}/resume`, { method: "POST" });
    if (!res.ok) {
      setError("Resume request failed");
      return;
    }
    setMessage("Resumed successfully");
  }

  return (
    <section>
      <h2>Emergency control</h2>
      <form onSubmit={stopAgent}>
        <div>
          <label>Reason</label>
          <input value={reason} onChange={(e) => setReason(e.target.value)} required />
        </div>
        <div>
          <label>Until (optional)</label>
          <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
        <button type="submit">Stop</button>
      </form>
      <button onClick={resumeAgent}>Resume</button>
      {message ? <p>{message}</p> : null}
      {error ? <p style={{ color: "red" }}>{error}</p> : null}
    </section>
  );
}
