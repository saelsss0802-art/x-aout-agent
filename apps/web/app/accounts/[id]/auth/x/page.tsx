import Link from "next/link";

type Props = {
  params: { id: string };
};

async function getStatus(accountId: string) {
  const apiBase = process.env.API_BASE_URL || "http://localhost:8000";
  const response = await fetch(`${apiBase}/oauth/x/status?account_id=${accountId}`, { cache: "no-store" });
  if (!response.ok) {
    return { connected: false };
  }
  return (await response.json()) as { connected: boolean; expires_at?: string };
}

export default async function XAuthPage({ params }: Props) {
  const status = await getStatus(params.id);
  const apiBase = process.env.API_BASE_URL || "http://localhost:8000";
  const connectHref = `${apiBase}/oauth/x/start?account_id=${params.id}`;

  return (
    <main>
      <h1>X OAuth Connection</h1>
      <p>Account ID: {params.id}</p>
      <p>Status: {status.connected ? "connected" : "disconnected"}</p>
      <p>Expires at: {status.expires_at || "-"}</p>
      <Link href={connectHref}>Connect X</Link>
    </main>
  );
}
