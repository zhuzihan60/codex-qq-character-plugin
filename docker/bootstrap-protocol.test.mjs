// Fake pipes only; this does not start Codex or verify Docker.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {diagnosticConnection} from './bootstrap-protocol.mjs';

function fakeChild() {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.kills = [];
  child.kill = signal => { child.kills.push(signal); return true; };
  return child;
}
test('child close rejects pending and future requests', async () => {
  const child = fakeChild();
  const rpc = diagnosticConnection(child);
  const pending = rpc.request('initialize',{});
  child.emit('close',1);
  await assert.rejects(pending,/closed before/);
  await assert.rejects(rpc.request('skills/list',{}),/closed before/);
  rpc.close();
});
test('spawn error rejects every pending request', async () => {
  const child = fakeChild();
  const rpc = diagnosticConnection(child);
  const first = rpc.request('initialize',{});
  const second = rpc.request('skills/list',{});
  child.emit('error',new Error('ENOENT'));
  await assert.rejects(first,/Cannot start/);
  await assert.rejects(second,/Cannot start/);
  rpc.close();
});
test('deadline rejects pending requests and kills unresponsive child', async () => {
  const child = fakeChild();
  const rpc = diagnosticConnection(child,10);
  await assert.rejects(rpc.request('initialize',{}),/deadline exceeded/);
  assert.deepEqual(child.kills,['SIGKILL']);
  rpc.close();
});
test('successful reply resolves only its request', async () => {
  const child = fakeChild();
  const rpc = diagnosticConnection(child);
  const result = rpc.request('initialize',{});
  child.stdout.write(JSON.stringify({id:1,result:{codexHome:'/home/character/.codex'}})+'\n');
  assert.deepEqual(await result,{codexHome:'/home/character/.codex'});
  rpc.close();
});
