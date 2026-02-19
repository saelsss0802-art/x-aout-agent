"use client";

import { useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const NUMBER_FIELDS = [
  "posts_per_day",
  "plan_thread_ratio",
  "plan_reply_ratio",
  "plan_quote_ratio",
  "x_search_max",
  "web_search_max",
  "web_fetch_max",
  "posting_poll_seconds",
  "reply_quote_daily_max",
];

export default function AgentSettingsForm({ agent }) {
  const initial = useMemo(
    () => ({
      daily_budget: agent.daily_budget ?? 300,
      auto_post: Boolean(agent.feature_toggles?.auto_post),
      posts_per_day: agent.feature_toggles?.posts_per_day ?? 1,
      plan_thread_ratio: agent.feature_toggles?.plan_thread_ratio ?? 0,
      plan_reply_ratio: agent.feature_toggles?.plan_reply_ratio ?? 0,
      plan_quote_ratio: agent.feature_toggles?.plan_quote_ratio ?? 0,
      x_search_max: agent.feature_toggles?.x_search_max ?? 10,
      web_search_max: agent.feature_toggles?.web_search_max ?? 10,
      web_fetch_max: agent.feature_toggles?.web_fetch_max ?? 3,
      posting_poll_seconds: agent.feature_toggles?.posting_poll_seconds ?? 300,
      reply_quote_daily_max: agent.feature_toggles?.reply_quote_daily_max ?? 3,
    }),
    [agent],
  );
  const [form, setForm] = useState(initial);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const onSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      const feature_toggles = { auto_post: form.auto_post };
      for (const key of NUMBER_FIELDS) {
        feature_toggles[key] = Number(form[key]);
      }
      const res = await fetch(`${API_BASE}/api/agents/${agent.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ daily_budget: Number(form.daily_budget), feature_toggles }),
      });
      if (!res.ok) {
        throw new Error("save_failed");
      }
      setMessage("Saved. Reload to reflect latest values.");
    } catch {
      setMessage("Save failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section>
      <h2>運用設定</h2>
      <p>
        <label>
          daily_budget
          <input
            type="number"
            value={form.daily_budget}
            onChange={(e) => setForm({ ...form, daily_budget: e.target.value })}
          />
        </label>
      </p>
      <p>
        <label>
          auto_post
          <input
            type="checkbox"
            checked={form.auto_post}
            onChange={(e) => setForm({ ...form, auto_post: e.target.checked })}
          />
        </label>
      </p>
      {NUMBER_FIELDS.map((field) => (
        <p key={field}>
          <label>
            {field}
            <input
              type="number"
              step={field.includes("ratio") ? "0.01" : "1"}
              value={form[field]}
              onChange={(e) => setForm({ ...form, [field]: e.target.value })}
            />
          </label>
        </p>
      ))}
      <button type="button" onClick={onSave} disabled={saving}>
        {saving ? "Saving..." : "Save"}
      </button>
      <p>{message}</p>
    </section>
  );
}
