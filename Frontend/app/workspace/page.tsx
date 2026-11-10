import { Suspense } from "react";
import { Workspace } from "@/components/workspace";
export default function WorkspacePage() { return <Suspense fallback={<p className="shell">Opening data workspace…</p>}><Workspace/></Suspense>; }
