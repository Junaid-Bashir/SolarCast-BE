import Link from "next/link";
import { ArrowUpRight, Sun } from "lucide-react";
export function Brand() { return <Link href="/" className="brand" aria-label="SolarCast home"><span className="brand-icon"><Sun size={23}/></span>solarcast<span className="brand-tag">PLATFORM</span></Link>; }
export function Header() { return <header className="header"><Brand/><nav aria-label="Main navigation"><Link href="/#workflow">How it works</Link><Link href="/workspace">Data workspace <ArrowUpRight size={14}/></Link></nav><span className="local-badge"><i/> Local development</span></header>; }
