import type { ReactNode } from 'react';

import styles from './Markdown.module.css';

/**
 * A small, safe Markdown renderer for the assistant's answers. It builds React elements only (no HTML is ever parsed or
 * injected) and supports paragraphs, headings, bullet and numbered lists, fenced code, `code`, **bold**, *italic* and
 * http(s) links. Unclosed markers (an answer still streaming) stay as plain text.
 */

const SAFE_URL = /^https?:\/\/[^\s<>"]+$/iu;
const LINK = /^\[([^\]\n]+)\]\(([^)\s]+)\)/u;

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let plain = '';
  let key = 0;
  const flush = (): void => {
    if (plain !== '') out.push(plain);
    plain = '';
  };
  let i = 0;
  while (i < text.length) {
    const rest = text.slice(i);
    const ch = text.charAt(i);
    if (ch === '`') {
      const end = text.indexOf('`', i + 1);
      if (end > i + 1) {
        flush();
        out.push(<code key={key++}>{text.slice(i + 1, end)}</code>);
        i = end + 1;
        continue;
      }
    } else if (rest.startsWith('**')) {
      const end = text.indexOf('**', i + 2);
      if (end > i + 2) {
        flush();
        out.push(<strong key={key++}>{renderInline(text.slice(i + 2, end))}</strong>);
        i = end + 2;
        continue;
      }
    } else if (ch === '*' && !/\s/u.test(text.charAt(i + 1))) {
      const end = text.indexOf('*', i + 1);
      if (end > i + 1 && text.charAt(end + 1) !== '*') {
        flush();
        out.push(<em key={key++}>{renderInline(text.slice(i + 1, end))}</em>);
        i = end + 1;
        continue;
      }
    } else if (ch === '[') {
      const match = LINK.exec(rest);
      if (match?.[1] !== undefined && match[2] !== undefined && SAFE_URL.test(match[2])) {
        flush();
        out.push(
          <a key={key++} href={match[2]} target="_blank" rel="noopener noreferrer">
            {renderInline(match[1])}
          </a>,
        );
        i += match[0].length;
        continue;
      }
    }
    plain += ch;
    i += 1;
  }
  flush();
  return out;
}

const FENCE = /^\s*```/u;
const HEADING = /^(#{1,6})\s+(.*)$/u;
const BULLET = /^\s*[-*+]\s+(.*)$/u;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/u;

export function Markdown({ text }: { text: string }): React.JSX.Element {
  const lines = text.replace(/\r\n?/gu, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let key = 0;
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? '';
    const heading = HEADING.exec(line);
    if (FENCE.test(line)) {
      // An unclosed fence (still streaming) runs to the end of the text.
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i] ?? '')) body.push(lines[i++] ?? '');
      i += 1;
      blocks.push(
        <pre key={key++}>
          <code>{body.join('\n')}</code>
        </pre>,
      );
    } else if (heading) {
      // Headings are shown small: an answer is not a page, so # becomes h3.
      const Tag = `h${String(Math.min(heading[1]?.length ?? 1, 4) + 2)}` as 'h3';
      blocks.push(<Tag key={key++}>{renderInline(heading[2] ?? '')}</Tag>);
      i += 1;
    } else if (BULLET.test(line) || NUMBERED.test(line)) {
      const ordered = !BULLET.test(line);
      const re = ordered ? NUMBERED : BULLET;
      const items: ReactNode[] = [];
      while (i < lines.length && re.test(lines[i] ?? '')) {
        items.push(<li key={items.length}>{renderInline(re.exec(lines[i] ?? '')?.[1] ?? '')}</li>);
        i += 1;
      }
      blocks.push(ordered ? <ol key={key++}>{items}</ol> : <ul key={key++}>{items}</ul>);
    } else if (line.trim() === '') {
      i += 1;
    } else {
      const para: string[] = [];
      while (
        i < lines.length &&
        (lines[i] ?? '').trim() !== '' &&
        !FENCE.test(lines[i] ?? '') &&
        !HEADING.test(lines[i] ?? '') &&
        !BULLET.test(lines[i] ?? '') &&
        !NUMBERED.test(lines[i] ?? '')
      ) {
        para.push(lines[i++] ?? '');
      }
      blocks.push(<p key={key++}>{renderInline(para.join('\n'))}</p>);
    }
  }
  return <div className={styles.md}>{blocks}</div>;
}
