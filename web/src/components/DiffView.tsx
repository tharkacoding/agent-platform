export function DiffView({ diff, title }: { diff: string; title?: string }) {
  const lines = diff.split("\n");
  return (
    <div>
      {title && <div className="text-xs text-muted mb-1 mono">{title}</div>}
      <pre className="block mono">
        {lines.map((l, i) => {
          const cls =
            l.startsWith("+") && !l.startsWith("+++")
              ? "diff-add"
              : l.startsWith("-") && !l.startsWith("---")
                ? "diff-del"
                : l.startsWith("@@")
                  ? "diff-hunk"
                  : l.startsWith("diff ") || l.startsWith("+++") || l.startsWith("---")
                    ? "font-semibold"
                    : "";
          return (
            <div key={i} className={cls}>
              {l || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}
