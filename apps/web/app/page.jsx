import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <h1>Operations Dashboard</h1>
      <p>
        <Link href="/dashboard">Go to dashboard</Link>
      </p>
    </main>
  );
}
