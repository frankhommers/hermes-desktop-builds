// Vitest's JSON reporter omits unhandled run errors; preserve them as a separate gate.
import fs from 'node:fs';
export default class CompletionReporter {
  onTestRunEnd(_modules, unhandledErrors, reason) {
    if(!process.env.DESKTOP_TEST_RECEIPT) throw new Error('Missing test receipt destination');
    fs.writeFileSync(process.env.DESKTOP_TEST_RECEIPT,JSON.stringify({
      reason,
      unhandledErrors:unhandledErrors.map(error=>String(error?.stack||error?.message||error)),
    },null,2)+'\n');
  }
}
