import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Bundle shape is a deliverable here, not an afterthought — see
// docs/performance-budget.md. Three decisions worth stating.
//
// 1. Everything from node_modules goes in ONE vendor chunk, and app code in
//    one more. Two JS requests, not five. On the budget's 400 ms RTT link a
//    round trip costs more than the bytes a finer split would save, and the
//    framework is a single cache entry that survives an app-code redeploy.
// 2. No CSS code-splitting. The stylesheet is ~1.5 KB gzipped and every
//    screen needs the evidence-class rules; splitting it trades one small
//    request for several smaller ones.
// 3. es2018 target. A low-end Android in the deployment context this
//    concept targets may be several Chrome versions behind, and the bytes
//    saved by targeting esnext are not worth a blank screen on a device
//    that cannot parse the bundle.
export default defineConfig({
  plugins: [react()],
  // Vitest reads this file too. jsdom rather than a real browser: these tests
  // assert component contracts -- which props exist, what never renders --
  // not layout, so a browser would cost minutes per run and prove nothing
  // extra. The visual rules in §9.1/§9.2 are asserted against the stylesheet
  // itself in tests/web/test_render.py.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
  },
  build: {
    target: 'es2018',
    cssCodeSplit: false,
    reportCompressedSize: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) return 'vendor'
          return undefined
        },
      },
    },
  },
})
