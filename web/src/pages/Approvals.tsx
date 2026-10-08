import { useEffect, useState } from "react";
import { api, type Approval } from "../api";
import { useApp } from "../state";
import { ApprovalCard } from "../components/ApprovalCard";
import { Empty, Page, PageHeader } from "../components/ui";

export default function Approvals() {
  const { approvals, refreshApprovals } = useApp();
  const [history, setHistory] = useState<Approval[]>([]);
  const loadHistory = () => api.approvals().then((all) => setHistory(all.filter((a) => a.status !== "pending").slice(0, 30))).catch(() => {});
  useEffect(() => {
    refreshApprovals();
    loadHistory();
  }, [refreshApprovals]);
  return (
    <Page>
      <PageHeader title="Approvals" subtitle="Risky actions pause here until you decide: destructive commands, new network domains, host browser, desktop control, and new agents." />
      <div className="space-y-3">
        {approvals.length === 0 && <Empty>Nothing waiting for approval.</Empty>}
        {approvals.map((a) => (
          <ApprovalCard key={a.id} a={a} onDecided={loadHistory} />
        ))}
      </div>
      {history.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-muted mt-10 mb-3">Recent decisions</h2>
          <div className="space-y-3 opacity-80">
            {history.map((a) => (
              <ApprovalCard key={a.id} a={a} />
            ))}
          </div>
        </>
      )}
    </Page>
  );
}
