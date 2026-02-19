import Link from "next/link";
import AgentControls from "../../components/agent-controls";
import AgentSettingsForm from "../../components/agent-settings-form";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function loadAgent(id) {
  const [agentRes, auditRes, defaultsRes] = await Promise.all([
    fetch(`${API_BASE}/api/agents/${id}`, { cache: "no-store" }),
    fetch(`${API_BASE}/api/agents/${id}/audit?limit=20`, { cache: "no-store" }),
    fetch(`${API_BASE}/api/config/defaults`, { cache: "no-store" }),
  ]);

  if (!agentRes.ok || !auditRes.ok || !defaultsRes.ok) {
    throw new Error("Failed to load agent detail");
  }

  return {
    agent: await agentRes.json(),
    audit: await auditRes.json(),
    defaults: await defaultsRes.json(),
  };
}

export default async function AgentDetailPage({ params }) {
  const { id } = params;
  const { agent, audit, defaults } = await loadAgent(id);

  return (
    <main>
      <p>
        <Link href="/dashboard">Back to dashboard</Link>
      </p>
      <h1>Agent {agent.id}</h1>
      <p>Status: {agent.status}</p>
      <p>Stop reason: {agent.stop_reason ?? "-"}</p>
      <p>Stopped at: {agent.stopped_at ?? "-"}</p>
      <p>Stop until: {agent.stop_until ?? "-"}</p>

      <AgentControls agentId={agent.id} />
      <p>Defaults: {JSON.stringify(defaults)}</p>
      <AgentSettingsForm agent={agent} />

      <h2>Recent PDCA (7d)</h2>
      <ul>
        {agent.daily_pdca.map((item) => (
          <li key={item.date}>
            <strong>{item.date}</strong>: {JSON.stringify(item.analytics_summary)}
          </li>
        ))}
      </ul>

      <h2>Audit logs</h2>
      <table border="1" cellPadding="6" style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th>Date</th>
            <th>Source</th>
            <th>Event</th>
            <th>Status</th>
            <th>Reason</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {audit.items.map((row, index) => (
            <tr key={`${row.created_at}-${index}`}>
              <td>{row.date}</td>
              <td>{row.source}</td>
              <td>{row.event_type}</td>
              <td>{row.status}</td>
              <td>{row.reason ?? ""}</td>
              <td>{row.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
