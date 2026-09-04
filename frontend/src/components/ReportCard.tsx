interface Props { report: Record<string, any> }

export default function ReportCard({ report }: Props) {
  const stats = [
    { label: '总轮次', value: report.rounds_total },
    { label: 'FLP 初', value: report.flp_initial },
    { label: 'FLP 终', value: report.flp_final },
    { label: '水剂消耗(L)', value: report.material_used },
    { label: '换电', value: report.swaps },
    { label: '补水', value: report.refills },
    { label: '重规划', value: report.replans },
  ]
  return (
    <div className="report-card">
      <div className="panel-title">📄 任务报告 <span className="plan-id">{report.report_id}</span></div>
      <div className="report-conclusion">{report.conclusion}</div>
      <div className="report-stats">
        {stats.map(s => (
          <div key={s.label} className="stat"><b>{s.value}</b><span>{s.label}</span></div>
        ))}
      </div>
      <details>
        <summary>全链路时间线({report.timeline?.length ?? 0} 轮)</summary>
        <ul className="report-timeline">
          {(report.timeline ?? []).map((t: any) => (
            <li key={t.round}><b>t+{t.t_min}min</b> · FLP→{t.flp}{t.events?.length ? ` · ${t.events.join(';')}` : ''}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}
