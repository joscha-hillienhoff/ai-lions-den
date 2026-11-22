import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "The AI Lion's Den",
  description:
    "A live startup pitch game: teams face a jury of four AI investors, defend their idea and compete for a fictional 250,000 EUR investment while the audience picks the winner.",
};

export const viewport: Viewport = {
  themeColor: "#141311",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
