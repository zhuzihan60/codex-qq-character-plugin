import {createInterface} from 'node:readline';

// Bounded, inference-free diagnostic connection used only by bootstrap.
export function diagnosticConnection(child, timeoutMs = 30000) {
  const pending = new Map();
  let serial = 0;
  let terminalError = null;
  const lines = createInterface({input:child.stdout});
  function fail(error) {
    terminalError ||= error;
    for (const waiter of pending.values()) waiter.reject(terminalError);
    pending.clear();
  }
  child.on('error', () => fail(new Error('Cannot start App Server diagnostic process')));
  child.on('close', () => fail(new Error('App Server closed before diagnostics completed')));
  child.stdin.on('error', () => fail(new Error('App Server diagnostic input pipe failed')));
  child.stdout.on('error', () => fail(new Error('App Server diagnostic output pipe failed')));
  lines.on('line', line => {
    let value;
    try { value = JSON.parse(line); } catch {
      fail(new Error('Invalid App Server diagnostic JSONL'));
      return;
    }
    const waiter = pending.get(value.id);
    if (waiter && !value.method) {
      pending.delete(value.id);
      value.error ? waiter.reject(new Error('App Server diagnostic RPC failed')) : waiter.resolve(value.result);
    }
  });
  const timeout = setTimeout(() => {
    fail(new Error('App Server diagnostic deadline exceeded'));
    child.kill('SIGKILL');
  }, timeoutMs);
  function write(value) {
    if (terminalError) throw terminalError;
    child.stdin.write(JSON.stringify(value)+'\n');
  }
  return {
    request(method, params) {
      const id = ++serial;
      return new Promise((resolve,reject) => {
        if (terminalError) { reject(terminalError); return; }
        pending.set(id,{resolve,reject});
        try { write({id,method,params}); }
        catch (error) { fail(error); }
      });
    },
    notify(method, params) { write({method,params}); },
    close() {
      clearTimeout(timeout);
      fail(new Error('Diagnostic connection closed'));
      child.stdin.end();
      child.kill();
      lines.close();
    }
  };
}
