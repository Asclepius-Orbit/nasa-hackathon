import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Markdown } from './Markdown';

const html = (text: string): string => new XMLSerializer().serializeToString(render(<Markdown text={text} />).container);

describe('Markdown', () => {
  it('renders bold, italic and inline code', () => {
    const out = html('A **bold** and *soft* and `code`.');
    expect(out).toContain('<strong>bold</strong>');
    expect(out).toContain('<em>soft</em>');
    expect(out).toContain('<code>code</code>');
    expect(out).not.toContain('**');
  });

  it('renders headings, lists and code blocks', () => {
    const out = html('# Title\n\n- one\n- two\n\n1. first\n2. second\n\n```\nlet x = 1;\n```');
    expect(out).toContain('<h3>Title</h3>');
    expect(out).toContain('<ul><li>one</li><li>two</li></ul>');
    expect(out).toContain('<ol><li>first</li><li>second</li></ol>');
    expect(out).toContain('<pre><code>let x = 1;</code></pre>');
  });

  it('leaves unclosed markers as plain text while streaming', () => {
    const out = html('Half **bold and `code');
    expect(out).toContain('Half **bold and `code');
    expect(out).not.toContain('<strong>');
  });

  it('treats an unclosed code fence as code', () => {
    expect(html('```\nabc')).toContain('<pre><code>abc</code></pre>');
  });

  it('never passes raw HTML through and only links http(s)', () => {
    const out = html('<script>x</script> [ok](https://a.io) [bad](javascript:alert(1))');
    expect(out).not.toContain('<script>');
    expect(out).toContain('&lt;script&gt;');
    expect(out).toContain('<a href="https://a.io"');
    expect(out).not.toContain('href="javascript');
  });
});
