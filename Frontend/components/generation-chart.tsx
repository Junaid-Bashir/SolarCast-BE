"use client";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Brush } from "recharts";
export type ChartPoint = { timestamp: string; generation: number | null };
export function GenerationChart({ points, compact = false, frequencyMinutes }: { points: ChartPoint[]; compact?: boolean; frequencyMinutes?: number | null }) {
  // Text labels preserve source local times instead of silently applying the browser timezone.
  const tick = (value: string) => compact ? value.slice(11, 16) : `${value.slice(5,10)} ${value.slice(11,16)}`;
  // Null separators break the plotted line across missing intervals; source data is untouched.
  const plotted = points.flatMap((point, index) => {
    const previous = points[index - 1];
    if (previous && frequencyMinutes && Date.parse(point.timestamp) - Date.parse(previous.timestamp) > frequencyMinutes * 60000 * 1.01) {
      return [{ timestamp: previous.timestamp, generation: null }, point];
    }
    return [point];
  });
  return <div className={compact ? "chart compact-chart" : "chart"} role="img" aria-label="Interactive historical solar generation chart. Values in source units; use the table for exact source rows."><ResponsiveContainer width="100%" height="100%" minWidth={0}>
    <AreaChart data={plotted} margin={{ top: 15, right: 15, bottom: 0, left: -15 }}>
      <defs><linearGradient id={compact ? "heroFill" : "dataFill"} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#bcf878" stopOpacity={0.28}/><stop offset="100%" stopColor="#bcf878" stopOpacity={0}/></linearGradient></defs>
      <CartesianGrid vertical={false} stroke="#243230" strokeDasharray="3 5"/>
      <XAxis dataKey="timestamp" tickFormatter={tick} minTickGap={60} stroke="#788781" tickLine={false} axisLine={false} fontSize={11}/>
      <YAxis stroke="#788781" tickLine={false} axisLine={false} fontSize={11}/>
      <Tooltip labelFormatter={(value) => String(value).replace("T", " ")} contentStyle={{ background: "#14211e", border: "1px solid #3b5047", borderRadius: 8, color: "#edf3ec" }} formatter={(value) => [Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 }), "Generation (source unit)"]}/>
      <Area type="linear" dataKey="generation" stroke="#bcf878" strokeWidth={2} fill={`url(#${compact ? "heroFill" : "dataFill"})`} connectNulls={false} isAnimationActive={false}/>
      {!compact && <Brush dataKey="timestamp" height={25} stroke="#6d886c" fill="#14211e" tickFormatter={tick} travellerWidth={8}/>}
    </AreaChart>
  </ResponsiveContainer></div>;
}
