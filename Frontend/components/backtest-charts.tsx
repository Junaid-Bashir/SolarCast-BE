"use client";
import { Bar, BarChart, Brush, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BacktestResults, ModelName } from "@/lib/api";

const tooltip = { background: "#14211e", border: "1px solid #3b5047", borderRadius: 8, color: "#edf3ec" };
export function BacktestChart({ results, model }: { results: BacktestResults; model: ModelName }) {
  const points = results.points.map(p=>({timestamp:p.timestamp, actual:p.actual_generation, predicted:p.predictions[model]}));
  const tick = (s:string)=>`${s.slice(5,10)} ${s.slice(11,16)}`;
  return <div className="chart backtest-chart" role="img" aria-label="Historical holdout chart comparing actual generation with predictions at the selected horizon"><ResponsiveContainer width="100%" height="100%" minWidth={0}><LineChart data={points} margin={{top:15,right:15,bottom:0,left:-10}}>
    <CartesianGrid vertical={false} stroke="#293a30" strokeDasharray="3 5"/>
    <XAxis dataKey="timestamp" tickFormatter={tick} stroke="#96a49b" fontSize={10} minTickGap={65} axisLine={false} tickLine={false}/>
    <YAxis stroke="#96a49b" fontSize={10} axisLine={false} tickLine={false}/>
    <Tooltip labelFormatter={v=>String(v).replace("T"," ")} contentStyle={tooltip} formatter={(v)=>Number(v).toLocaleString(undefined,{maximumFractionDigits:3})}/>
    <Legend wrapperStyle={{fontSize:11,paddingTop:10,paddingBottom:10}}/>
    <Line name="Actual generation" dataKey="actual" stroke="#8eaaa1" strokeWidth={2} dot={false} isAnimationActive={false}/>
    <Line name="Predicted generation" dataKey="predicted" stroke="#c5f58a" strokeWidth={2} dot={false} strokeDasharray="5 3" isAnimationActive={false}/>
    <Brush dataKey="timestamp" tickFormatter={tick} height={25} stroke="#6d886c" fill="#14211e" travellerWidth={8}/>
  </LineChart></ResponsiveContainer></div>;
}
export function MetricChart({results,metric}:{results:BacktestResults;metric:"mae"|"rmse"|"skill"}) {
  const data = results.metrics.filter(m=>m[metric] !== null);
  return <div className="metric-chart" role="img" aria-label={`${metric.toUpperCase()} by model`}><ResponsiveContainer width="100%" height="100%" minWidth={0}><BarChart data={data} layout="vertical" margin={{left:0,right:30,top:10,bottom:0}}>
    <CartesianGrid horizontal={false} stroke="#293a30" strokeDasharray="3 5"/>
    <XAxis type="number" stroke="#96a49b" fontSize={10} tickLine={false} axisLine={false}/>
    <YAxis type="category" dataKey="label" width={100} stroke="#b7c7b5" fontSize={11} tickLine={false} axisLine={false}/>
    <Tooltip contentStyle={tooltip} cursor={{fill:"#25362b"}} formatter={(v)=>`${Number(v).toFixed(3)}${metric === "skill" ? "%" : ""}`}/>
    <Bar dataKey={metric} name={metric === "skill" ? "RMSE skill vs persistence (%)" : metric.toUpperCase()} radius={[0,3,3,0]} maxBarSize={21} isAnimationActive={false}>{data.map(m=><Cell key={m.model} fill={m.model === results.best_model ? "#c5f58a" : "#617d69"}/>)}</Bar>
  </BarChart></ResponsiveContainer></div>;
}
