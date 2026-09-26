import "./globals.css";
export const metadata = { title: "Chip Harness", description: "A self-evolving chip-design harness on MongoDB Atlas" };
export default function RootLayout({ children }) {
  return (<html lang="en"><body>{children}</body></html>);
}
