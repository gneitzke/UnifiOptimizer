import type { CSSProperties, ReactNode } from 'react';

/**
 * A small, dependency-free Markdown renderer for investigation dossiers and
 * responses. It builds React elements only — it NEVER uses
 * `dangerouslySetInnerHTML` — so all text is escaped by React and no raw HTML in
 * the source can inject markup. Link hrefs are sanitized to `http/https/mailto`;
 * anything else renders as plain text.
 *
 * Supported: fenced code, ATX headings, GFM tables, ordered/unordered lists,
 * blockquotes, thematic breaks, paragraphs; inline `code`, **bold**, *italic* /
 * _italic_, and `[text](href)`. Unknown syntax degrades to text — acceptable for
 * an admin-facing analysis surface, and safe by construction.
 */

const HREF_OK = /^(https?:|mailto:)/i;

const mono: CSSProperties = { fontFamily: 'var(--font-mono)' };

function sanitizeHref(href: string): string | null {
  const trimmed = href.trim();
  return HREF_OK.test(trimmed) ? trimmed : null;
}

/** Split `text` by `regex` (one capture group), mapping matches via `onMatch`. */
function splitBy(
  text: string,
  regex: RegExp,
  onMatch: (groups: RegExpExecArray, key: string) => ReactNode,
  onRest: (rest: string, key: string) => ReactNode[],
  keyPrefix: string,
): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let idx = 0;
  const re = new RegExp(regex.source, regex.flags.includes('g') ? regex.flags : regex.flags + 'g');
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(...onRest(text.slice(last, m.index), `${keyPrefix}r${idx}`));
    out.push(onMatch(m, `${keyPrefix}m${idx}`));
    last = m.index + m[0].length;
    idx += 1;
    if (m[0].length === 0) re.lastIndex += 1; // guard against zero-width loops
  }
  if (last < text.length) out.push(...onRest(text.slice(last), `${keyPrefix}r${idx}`));
  return out;
}

function renderItalic(text: string, keyPrefix: string): ReactNode[] {
  return splitBy(
    text,
    /\*([^*\n]+)\*|_([^_\n]+)_/,
    (g, key) => <em key={key}>{g[1] ?? g[2]}</em>,
    (rest) => [rest],
    keyPrefix,
  );
}

function renderBold(text: string, keyPrefix: string): ReactNode[] {
  return splitBy(
    text,
    /\*\*([^\n]+?)\*\*/,
    (g, key) => <strong key={key}>{renderItalic(g[1], key)}</strong>,
    (rest, key) => renderItalic(rest, key),
    keyPrefix,
  );
}

function renderLinks(text: string, keyPrefix: string): ReactNode[] {
  return splitBy(
    text,
    /\[([^\]]+)\]\(([^)\s]+)\)/,
    (g, key) => {
      const href = sanitizeHref(g[2]);
      if (href === null) return <span key={key}>{g[1]}</span>;
      return (
        <a key={key} href={href} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>
          {g[1]}
        </a>
      );
    },
    (rest, key) => renderBold(rest, key),
    keyPrefix,
  );
}

/** Inline: code spans first (opaque), then links → bold → italic inside the rest. */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return splitBy(
    text,
    /`([^`]+)`/,
    (g, key) => (
      <code
        key={key}
        style={{
          ...mono,
          fontSize: '0.9em',
          background: 'var(--canvas)',
          padding: '0.05em 0.35em',
          borderRadius: 4,
        }}
      >
        {g[1]}
      </code>
    ),
    (rest, key) => renderLinks(rest, key),
    keyPrefix,
  );
}

function splitCells(row: string): string[] {
  let cells = row.split('|');
  if (cells.length && cells[0].trim() === '') cells = cells.slice(1);
  if (cells.length && cells[cells.length - 1].trim() === '') cells = cells.slice(0, -1);
  return cells.map((c) => c.trim());
}

const isTableSeparator = (line: string): boolean =>
  /^\s*\|?[\s:]*-{1,}[\s:|-]*\|?\s*$/.test(line) && line.includes('-');

const hairlineCell: CSSProperties = {
  border: '1px solid var(--hairline)',
  padding: '6px 10px',
  textAlign: 'left',
  verticalAlign: 'top',
};

export function SafeMarkdown({ markdown }: { markdown: string }): ReactNode {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  const nextKey = () => `b${key++}`;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fence = line.match(/^\s*```/);
    if (fence) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1; // consume closing fence
      blocks.push(
        <pre
          key={nextKey()}
          style={{
            ...mono,
            fontSize: 12,
            background: 'var(--canvas)',
            border: '1px solid var(--hairline)',
            borderRadius: 'var(--radius-control)',
            padding: '10px 12px',
            overflowX: 'auto',
          }}
        >
          <code style={mono}>{body.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    // Blank line
    if (line.trim() === '') {
      i += 1;
      continue;
    }

    // Thematic break
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push(<hr key={nextKey()} style={{ border: 'none', borderTop: '1px solid var(--hairline)', margin: '4px 0' }} />);
      i += 1;
      continue;
    }

    // Heading
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const sizes = [18, 16, 15, 14, 13, 13];
      blocks.push(
        <div
          key={nextKey()}
          style={{
            color: 'var(--fg)',
            fontWeight: 600,
            fontSize: sizes[level - 1],
            marginTop: blocks.length ? 8 : 0,
            letterSpacing: '-0.01em',
          }}
        >
          {renderInline(heading[2], nextKey())}
        </div>,
      );
      i += 1;
      continue;
    }

    // Table (header row followed by a separator row)
    if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = splitCells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitCells(lines[i]));
        i += 1;
      }
      blocks.push(
        <div key={nextKey()} style={{ overflowX: 'auto' }}>
          <table
            style={{
              borderCollapse: 'collapse',
              fontSize: 13,
              width: 'auto',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            <thead>
              <tr>
                {header.map((h, hi) => (
                  <th
                    key={hi}
                    style={{ ...hairlineCell, color: 'var(--fg-muted)', fontWeight: 600, background: 'var(--canvas)' }}
                  >
                    {renderInline(h, `th${hi}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci} style={{ ...hairlineCell, color: 'var(--fg)' }}>
                      {renderInline(c, `td${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      blocks.push(
        <blockquote
          key={nextKey()}
          style={{
            borderLeft: '3px solid var(--hairline)',
            paddingLeft: 12,
            color: 'var(--fg-muted)',
            margin: 0,
          }}
        >
          {renderInline(body.join(' '), nextKey())}
        </blockquote>,
      );
      continue;
    }

    // Lists (unordered or ordered)
    const orderedStart = /^\s*\d+\.\s+/.test(line);
    const bulletStart = /^\s*[-*+]\s+/.test(line);
    if (orderedStart || bulletStart) {
      const items: string[] = [];
      const itemRe = orderedStart ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
      while (i < lines.length && itemRe.test(lines[i])) {
        items.push(lines[i].replace(itemRe, '$1'));
        i += 1;
      }
      const listStyle: CSSProperties = { margin: 0, paddingLeft: 20, color: 'var(--fg)', display: 'flex', flexDirection: 'column', gap: 2 };
      const children = items.map((it, ii) => (
        <li key={ii} style={{ lineHeight: 1.5 }}>
          {renderInline(it, `li${ii}`)}
        </li>
      ));
      blocks.push(
        orderedStart ? (
          <ol key={nextKey()} style={listStyle}>
            {children}
          </ol>
        ) : (
          <ul key={nextKey()} style={listStyle}>
            {children}
          </ul>
        ),
      );
      continue;
    }

    // Paragraph (gather consecutive plain lines)
    const para: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^\s*```/.test(lines[i]) &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*([-*_])\1{2,}\s*$/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={nextKey()} style={{ margin: 0, color: 'var(--fg)', lineHeight: 1.55 }}>
        {renderInline(para.join(' '), nextKey())}
      </p>,
    );
  }

  return <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 720 }}>{blocks}</div>;
}
