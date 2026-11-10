import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "SolarCast — Your data. A clearer horizon.", description: "Inspect solar production data, compare forecasting models, and generate future solar-generation forecasts from your own history." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
