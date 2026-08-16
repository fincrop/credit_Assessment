import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import ReduxProvider from "./components/providers/ReduxProvider";
import AuthProvider from "./components/providers/AuthProvider";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AgriCredit Platform",
  description: "AgriStack data acquisition and farmer credit assessment",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {/* First tab stop on every page. A dashboard with a long farm list
            is otherwise ~40 tabs deep before the content starts. */}
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <ReduxProvider>
          <AuthProvider>
            <div id="main">{children}</div>
          </AuthProvider>
        </ReduxProvider>
      </body>
    </html>
  );
}
