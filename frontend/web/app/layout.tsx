import type { Metadata } from "next";
// Fonts are served from this origin, not fonts.googleapis.com — see the header
// of fonts.css. Loading them from Google leaked every visitor's IP to Google on
// every page view, which /privacy now says we don't do. Keep this import above
// globals.css so the @font-face rules are declared before anything uses them.
import "./fonts.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "EURAG — EU SME Intelligence Hub",
  description: "Citation-first answers on EU compliance and funding for SMEs.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
