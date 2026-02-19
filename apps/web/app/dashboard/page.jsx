import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function loadAgents() {
  const res = await fetch(`${API_BASE}/api/agents`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to load agents");
  }
  return res.json();
}

export default async function DashboardPage() {
  const data = await loadAgents();

  return (
    <main>
      <h1>Dashboard</h1>
      <p>App-wide X usage units today: {data.app_wide_usage?.x_usage_units ?? "-"}</p>
      <table border="1" cellPadding="6" style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th>Agent ID</th>
            <th>Status</th>
            <th>Stop reason</th>
            <th>Budget</th>
            <th>Today estimate</th>
            <th>Measured units</th>
            <th>Latest PDCA</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {data.agents.map((agent) => (
            <tr key={agent.id}>
              <td>{agent.id}</td>
              <td>{agent.status}</td>
              <td>{agent.stop_reason ?? ""}</td>
              <td>{agent.daily_budget}</td>
              <td>{agent.today_cost?.total ?? 0}</td>
              <td>{agent.today_cost?.x_usage_units ?? "-"}</td>
              <td>{agent.latest_pdca_date ?? "-"}</td>
              <td>
                <Link href={`/agents/${agent.id}`}>detail</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
