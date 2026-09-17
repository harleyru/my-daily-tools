#!/usr/bin/env python3
"""Lightweight Markdown -> HTML renderer (pure stdlib, no pip dependencies).

Covers the subset that LLM research/Q&A output actually uses:
  ATX headings #~###### / bold ** / italic * / inline code ` / fenced code blocks ``` /
  tables | | / ordered and unordered lists (2-space indent nesting) / quotes > /
  horizontal rules --- / links [t](u) / paragraphs

Usage:
    from markdown_render import md_to_html
    html_body = md_to_html(text)   # returns a <body> fragment, already HTML-escaped, safe to embed

Order of operations: escape the whole document first -> protect fenced code blocks -> tables ->
headings/lists/quotes/rules/paragraphs -> inline formatting.
"""

import html as _html
import re

__all__ = ["md_to_html"]

_FENCE = re.compile(r"^```(\w*)\s*$")

_inline_rules = [
    (re.compile(r"`([^`\n]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*\n]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\*([^*\n]+)\*"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)"),
     r'<a href="\2" target="_blank" rel="noopener">\1</a>'),
    # The full-width parens (U+FF08/U+FF09) are not a typo: they let a bare URL
    # still autolink when it is wrapped in CJK punctuation. Harmless for pure
    # English text -- it only widens the match slightly.
    (re.compile(r"(^|[\s(（])(https?://[^\s<)）]+)"),
     r'\1<a href="\2" target="_blank" rel="noopener">\2</a>'),
]


def _apply_inline(s):
    for pat, rep in _inline_rules:
        s = pat.sub(rep, s)
    return s


def _escape_cell(s):
    s = _html.escape(s)
    s = re.sub(r"<code>|</code>", "", s)
    return _apply_inline(s)


def _parse_table(lines, i):
    """lines[i] is the header row, lines[i+1] the separator -> (html, next index)."""
    header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    rows, j = [], i + 2
    while j < len(lines) and lines[j].strip().startswith("|"):
        rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
        j += 1
    out = ["<table><thead><tr>"]
    out += ["<th>%s</th>" % _escape_cell(c) for c in header]
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>")
        out += ["<td>%s</td>" % _escape_cell(c) for c in r]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out), j


def _split_fences(text):
    """Split fenced code blocks out line by line -> (text blocks, code segments).

    The two lists interleave: blocks[0] text -> codes[0] code -> blocks[1] text -> ...
    """
    blocks, codes, buf, fence = [], [], [], None
    for ln in text.split("\n"):
        m = _FENCE.match(ln)
        if m:
            if fence is None:
                fence = [m.group(1)]
            else:
                codes.append((fence[0], "\n".join(fence[1:])))
                blocks.append("\n".join(buf))
                buf, fence = [], None
            continue
        if fence is not None:
            fence.append(ln)
        else:
            buf.append(ln)
    if fence is not None:  # unclosed fence: restore it as plain text
        buf.append("```" + ("%s" % fence[0] if fence[0] else ""))
        buf.extend(fence[1:])
    if buf or not blocks:
        blocks.append("\n".join(buf))
    return blocks, codes


def md_to_html(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    escaped = _html.escape(text)

    blocks, codes = _split_fences(escaped)
    out = []
    for bi, blk in enumerate(blocks):
        if blk.strip():
            lines = blk.split("\n")
            i, in_list = 0, None
            while i < len(lines):
                raw = lines[i]

                def close_list():
                    nonlocal in_list
                    if in_list:
                        out.append("</%s>" % in_list)
                        in_list = None

                # table
                if (raw.strip().startswith("|") and i + 1 < len(lines)
                        and re.match(r"^\s*\|?[\s:|-]*-+\s*(\|\s*[\s:|-]*-+\s*)+\|?\s*$",
                                     lines[i + 1])):
                    close_list()
                    tbl, i = _parse_table(lines, i)
                    out.append(tbl)
                    continue
                # horizontal rule
                if re.match(r"^\s*-{3,}\s*$", raw) or re.match(r"^\s*\*{3,}\s*$", raw):
                    close_list()
                    out.append("<hr>")
                    i += 1
                    continue
                # heading
                m = re.match(r"^(#{1,6})\s+(.*)$", raw)
                if m:
                    close_list()
                    out.append("<h%d>%s</h%d>"
                               % (len(m.group(1)), _apply_inline(m.group(2)), len(m.group(1))))
                    i += 1
                    continue
                # blockquote (after html.escape, > has become &gt;)
                if raw.startswith("&gt;"):
                    close_list()
                    q = []
                    while i < len(lines) and lines[i].startswith("&gt;"):
                        q.append("<p>%s</p>" % _apply_inline(lines[i][4:].strip()))
                        i += 1
                    out.append("<blockquote>%s</blockquote>" % "".join(q))
                    continue
                # list
                li = re.match(r"^\s{0,2}([-*+]|\d+[.)])\s+(.*)$", raw)
                if li:
                    tag = "ol" if re.match(r"\d", li.group(1)) else "ul"
                    if in_list != tag:
                        if in_list:
                            out.append("</%s>" % in_list)
                        out.append("<%s>" % tag)
                        in_list = tag
                    out.append("<li>%s</li>" % _apply_inline(li.group(2)))
                    i += 1
                    continue
                # plain paragraph
                if not raw.strip():  # blank line: paragraph boundary, skip
                    i += 1
                    continue
                close_list()
                para = [raw]
                i += 1
                while (i < len(lines) and lines[i].strip()
                       and not re.match(r"^(#{1,6})\s", lines[i])
                       and not lines[i].strip().startswith("```")
                       and not re.match(r"^\s{0,2}([-*+]|\d+[.)])\s", lines[i])
                       and not lines[i].strip().startswith("|")
                       and not lines[i].startswith("&gt;")):
                    para.append(lines[i])
                    i += 1
                out.append("<p>%s</p>" % _apply_inline(" ".join(x.strip() for x in para)))
            if in_list:
                out.append("</%s>" % in_list)
        # code segment follows its text block
        if codes and bi < len(codes):
            lang, code = codes[bi]
            out.append("<pre><code%s>%s</code></pre>"
                       % (" class='lang-%s'" % lang if lang else "", code))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    sys.stdout.write(md_to_html(sys.stdin.read()))
