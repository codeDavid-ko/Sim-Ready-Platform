import "./globals.css";

export const metadata = { title: "algo-runner", description: "URL로 알고리즘 돌리기" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
