export default function RootLayout({ children }) {
  return (
    <html lang="ja">
      <body style={{ fontFamily: "sans-serif", margin: 20 }}>{children}</body>
    </html>
  );
}
