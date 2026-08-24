import { useEffect, useState } from 'react'

// Minimal async hook. No data-fetching library on purpose: react-query and
// friends are 12-40 KB gzipped for cache behaviour three read-only screens do
// not need, and docs/performance-budget.md budgets the whole JS payload at
// 100 KB. See that document's "What we did not install" section.
//
// Errors are returned, never swallowed — an empty screen with no explanation
// is the silent gap ARCHITECTURE.md §8 forbids.
export default function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    let live = true
    setState({ loading: true, data: null, error: null })
    Promise.resolve()
      .then(fn)
      .then((data) => live && setState({ loading: false, data, error: null }))
      .catch((error) => live && setState({ loading: false, data: null, error }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
