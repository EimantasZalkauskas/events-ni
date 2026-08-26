import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "What's On — Belfast",
  description: "Upcoming gigs, clubs and events in Belfast.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}