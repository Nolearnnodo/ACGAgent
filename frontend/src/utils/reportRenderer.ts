import katex from 'katex'
import { marked } from 'marked'

interface LatexToken {
  token: string
  html: string
}

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function renderFormula(source: string, displayMode: boolean): string {
  return katex.renderToString(source.trim(), {
    displayMode,
    throwOnError: false,
    strict: false,
    trust: false,
  })
}

function extractLatex(markdown: string): { markdown: string; tokens: LatexToken[] } {
  const tokens: LatexToken[] = []

  function pushToken(source: string, displayMode: boolean): string {
    const token = `@@ACG_LATEX_${tokens.length}@@`
    tokens.push({ token, html: renderFormula(source, displayMode) })
    return token
  }

  let next = markdown.replace(/(^|[^\\])\$\$([\s\S]+?)\$\$/g, (_match, prefix, formula) => (
    `${prefix}${pushToken(String(formula), true)}`
  ))

  next = next.replace(/(^|[^\\])\$([^\n$]+?)\$/g, (_match, prefix, formula) => (
    `${prefix}${pushToken(String(formula), false)}`
  ))

  return { markdown: next, tokens }
}

export function renderReportMarkdown(markdown: string): string {
  const { markdown: withTokens, tokens } = extractLatex(markdown || '')
  const escapedMarkdown = escapeHtml(withTokens)
  let html = marked.parse(escapedMarkdown, {
    async: false,
    breaks: true,
    gfm: true,
  }) as string

  for (const item of tokens) {
    html = html.replaceAll(item.token, item.html)
  }

  return html
}
