import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SkyNYC — live weather impact on NYC arrivals",
  description:
    "Real-time derived arrivals, holding and go-arounds over JFK, LGA and EWR, validated daily against independent ground truth — plus 38 years of history.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-page text-ink">{children}</body>
    </html>
  );
}
