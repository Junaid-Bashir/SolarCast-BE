"use client";
import { Brush, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FutureForecast } from "@/lib/api";

export function FutureChart({forecast}:{forecast:FutureForecast}) {
  const rows=[...forecast.context.map(p=>({timestamp:p.timestamp,observed:p.generation,future:null as number|null})),...forecast.points.map(p=>({timestamp:p.timestamp,observed:null as number|null,future:p.predicted_generation}))];
  const tick=(s:string)=>`${s.slice(5,10)} ${s.slice(11,16)}`;
  return <div className="chart future-chart" role="img" aria-label="Future solar generation chart. Observed history ends at the cutoff; dashed forecasts lie strictly after it."><ResponsiveContainer width="100%" height="100%" minWidth={0}><LineChart data={rows} margin={{top:25,right:20,left:-10,bottom:0}}>
    <CartesianGrid vertical={false} stroke="#273b3b" strokeDasharray="3 5"/>
    <XAxis dataKey="timestamp" tickFormatter={tick} minTickGap={65} fontSize={10} stroke="#96a49b" tickLine={false} axisLine={false}/>
    <YAxis fontSize={10} stroke="#96a49b" tickLine={false} axisLine={false}/>
    <Tooltip contentStyle={{background:"#122124",border:"1px solid #40616a",borderRadius:8,color:"#edf3ec"}} labelFormatter={s=>String(s).replace("T"," ")} formatter={v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:3})}/>
    <Legend wrapperStyle={{fontSize:11,paddingTop:10,paddingBottom:10}}/>
    <ReferenceArea x1={forecast.points[0]?.timestamp} x2={forecast.points.at(-1)?.timestamp} fill="#74dbe7" fillOpacity={.035}/>
    <ReferenceLine x={forecast.origin} stroke="#809c9f" strokeDasharray="4 4" label={{value:"Data cutoff",fill:"#b4c9cb",fontSize:10,position:"insideTopLeft"}}/>
    <Line name="Observed history" dataKey="observed" stroke="#839b92" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false}/>
    <Line name="Future forecast" dataKey="future" stroke="#8cdeea" dot={forecast.points.length===1?{r:4}:false} strokeWidth={2} strokeDasharray="5 3" connectNulls={false} isAnimationActive={false}/>
    <Brush dataKey="timestamp" ariaLabel="Future forecast time window: use arrow keys to adjust the range" height={25} stroke="#64898d" fill="#122124" tickFormatter={tick} travellerWidth={8}/>
  </LineChart></ResponsiveContainer></div>;
}
