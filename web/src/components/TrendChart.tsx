// 逐日成本/调用趋势图（M8 试点看板与用量页共用）
// 手写 SVG，不引图表库：一个折线+柱状组合不值得多挂 200KB 依赖。
export type TrendPoint = {
  date: string;
  cost: number;
  calls: number;
  tokens: number;
  real_calls?: number;
};

export function TrendChart({
  series,
  budgetAvg = 0,
  height = 176,
  unit = "¥",
}: {
  series: TrendPoint[];
  budgetAvg?: number;
  height?: number;
  unit?: string;
}) {
  const W = 720;
  const PL = 38, PR = 14, PT = 16, PB = 26;
  const innerW = W - PL - PR;
  const innerH = height - PT - PB;

  if (!series || series.length === 0) {
    return <div className="hint">暂无趋势数据 —— 跑几轮对话后这里会出现逐日曲线。</div>;
  }

  // 预算线只在「和实际用量同一量级」时才画：预算日均若是实际的几十倍，
  // 把它算进 Y 轴会把真实曲线压成一条贴地线，图就废了（实测 500 元/月预算 vs ¥0.03 用量）。
  const maxCost = Math.max(...series.map((s) => s.cost), 1e-6);
  const showBudget = budgetAvg > 0 && budgetAvg <= maxCost * 3;
  const yMax = showBudget ? Math.max(maxCost, budgetAvg) : maxCost;
  const maxCalls = Math.max(...series.map((s) => s.calls), 1);
  const bw = innerW / series.length;
  const x = (i: number) => PL + i * bw + bw / 2;
  const yCost = (v: number) => PT + innerH - (v / yMax) * innerH;
  const yCalls = (v: number) => PT + innerH - (v / maxCalls) * innerH * 0.82;

  const line = series.map((s, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${yCalls(s.calls).toFixed(1)}`).join(" ");
  const ticks = [0, 0.5, 1].map((f) => ({
    y: PT + innerH - f * innerH,
    label: (yMax * f).toFixed(yMax < 0.1 ? 4 : 2),
  }));

  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img"
      aria-label="逐日成本与调用趋势" style={{ display: "block" }}>
      {ticks.map((t) => (
        <g key={t.y}>
          <line x1={PL} y1={t.y} x2={W - PR} y2={t.y} stroke="#e8eff8" strokeWidth={1} />
          <text x={PL - 6} y={t.y + 3.5} fontSize={9} fill="#8ea3b8" textAnchor="end">{t.label}</text>
        </g>
      ))}

      {series.map((s, i) => (
        <rect key={s.date} x={PL + i * bw + bw * 0.18} y={yCost(s.cost)}
          width={Math.max(2, bw * 0.64)} height={Math.max(0, PT + innerH - yCost(s.cost))}
          fill={s.cost > 0 ? "#2d7ff9" : "#dce8f6"} opacity={0.9} rx={2}>
          <title>{`${s.date}　成本 ${unit}${s.cost.toFixed(4)}　调用 ${s.calls}　Token ${s.tokens}`}</title>
        </rect>
      ))}

      {showBudget && (
        <line x1={PL} y1={yCost(budgetAvg)} x2={W - PR} y2={yCost(budgetAvg)}
          stroke="#c62828" strokeWidth={1} strokeDasharray="5 4" opacity={0.75} />
      )}

      <path d={line} fill="none" stroke="#ef6c00" strokeWidth={1.6} opacity={0.85} />
      {series.map((s, i) => (
        <circle key={"c" + s.date} cx={x(i)} cy={yCalls(s.calls)} r={s.calls ? 2.2 : 0} fill="#ef6c00" />
      ))}

      {[0, Math.floor(series.length / 2), series.length - 1].map((i) => (
        <text key={"t" + i} x={x(i)} y={height - 8} fontSize={9.5} fill="#8ea3b8"
          textAnchor={i === 0 ? "start" : i === series.length - 1 ? "end" : "middle"}>
          {series[i].date.slice(5)}
        </text>
      ))}

      <g transform={`translate(${PL + 2},${PT - 4})`}>
        <rect x={0} y={-9} width={9} height={9} fill="#2d7ff9" rx={2} opacity={0.9} />
        <text x={13} y={-1} fontSize={9.5} fill="#5b7185">每日成本（{unit}）</text>
        <rect x={104} y={-9} width={9} height={3} fill="#ef6c00" rx={1.5} />
        <text x={117} y={-1} fontSize={9.5} fill="#5b7185">每日调用数</text>
        {showBudget && (
          <>
            <line x1={196} y1={-7} x2={210} y2={-7} stroke="#c62828" strokeDasharray="4 3" />
            <text x={214} y={-1} fontSize={9.5} fill="#5b7185">预算日均</text>
          </>
        )}
      </g>
    </svg>
  );
}
