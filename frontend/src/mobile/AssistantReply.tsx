import type { ReactNode } from "react";

/** A narrow Markdown display subset: paragraphs, bold text and pipe tables.
 * Every leaf stays a React text node. HTML, URLs, images and actions are never
 * interpreted or enabled by assistant output. */
function inline(value: string): ReactNode {
  return value.split(/(\*\*[^*\n]+\*\*)/g).map((part, i) => part.startsWith("**") && part.endsWith("**") && part.length > 4
    ? <strong key={i}>{part.slice(2, -2)}</strong> : part);
}
function cells(line: string): string[] | null {
  let value = line.trim();
  if (!value.includes("|")) return null;
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|") && !value.endsWith("\\|")) value = value.slice(0, -1);
  const result: string[] = []; let cell = "";
  for (let i = 0; i < value.length; i++) {
    if (value[i] === "\\" && value[i + 1] === "|") { cell += "|"; i++; }
    else if (value[i] === "|") { result.push(cell.trim()); cell = ""; }
    else cell += value[i];
  }
  result.push(cell.trim());
  return result.length >= 2 ? result : null;
}
export function AssistantReply({ text }: { text: string }) {
  const lines = text.split(/\r?\n/), blocks: ReactNode[] = [], paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) { blocks.push(<p key={blocks.length}>{inline(paragraph.join("\n"))}</p>); paragraph.length = 0; }
  };
  for (let i = 0; i < lines.length;) {
    const header = cells(lines[i]), separator = i + 1 < lines.length ? cells(lines[i + 1]) : null;
    if (header && separator?.length === header.length && separator.every(cell => /^:?-{3,}:?$/.test(cell))) {
      const body: string[][] = []; let end = i + 2;
      while (end < lines.length) {
        const row = cells(lines[end]);
        if (!row || row.length !== header.length) break;
        body.push(row); end++;
      }
      // Do not turn a malformed/empty table into invented data.
      if (body.length) {
        flush(); blocks.push(<div className="assistant-table-scroll" key={blocks.length} tabIndex={0} role="region" aria-label="助手回复表格，可横向滚动">
          <table><thead><tr>{header.map((cell, c) => <th scope="col" key={c}>{inline(cell)}</th>)}</tr></thead>
            <tbody>{body.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}>{inline(cell)}</td>)}</tr>)}</tbody></table>
        </div>); i = end; continue;
      }
    }
    if (!lines[i].trim()) flush(); else paragraph.push(lines[i]);
    i++;
  }
  flush(); return <div className="assistant-reply">{blocks}</div>;
}
