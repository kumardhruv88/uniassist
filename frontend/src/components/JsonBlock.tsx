import { useMemo } from 'react'

const TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?/g

/** Pretty-printed JSON with keys in a quieter tone. Values are inserted as text, never as HTML. */
export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const parts = useMemo(() => {
    const text = JSON.stringify(value, null, 2) ?? 'null'
    const out: { text: string; key: boolean }[] = []
    let last = 0
    for (const match of text.matchAll(TOKEN)) {
      const start = match.index ?? 0
      if (start > last) out.push({ text: text.slice(last, start), key: false })
      out.push({ text: match[0], key: Boolean(match[2]) })
      last = start + match[0].length
    }
    if (last < text.length) out.push({ text: text.slice(last), key: false })
    return out
  }, [value])

  return (
    <pre className="code-block" tabIndex={0} aria-label={label}>
      <code>
        {parts.map((part, i) =>
          part.key ? (
            <span key={i} className="json-key">
              {part.text}
            </span>
          ) : (
            part.text
          ),
        )}
      </code>
    </pre>
  )
}
