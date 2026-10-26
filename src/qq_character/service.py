"""One foreground bridge owns scheduling, workers and outbox; supervise externally."""
from __future__ import annotations
import asyncio
import contextlib
import time
from pathlib import Path


class ProcessLock:
    def __init__(self,path): self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b');self.file.seek(0)
        if self.path.stat().st_size==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if __import__('os').name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close();raise RuntimeError('bridge already running; stop it before maintenance') from exc
        return self
    def __exit__(self,*args):
        if self.file:self.file.close()


async def serve(config,store,engine):
    from .transport import OneBotTransport,NotConnected,AmbiguousDelivery
    wake=asyncio.Event()
    transport=OneBotTransport(config,store,on_event=wake.set)
    store.recover()
    async def worker():
        while True:
            engine.schedule()
            worked=await asyncio.to_thread(engine.step)
            if not worked:
                wake.clear()
                try:await asyncio.wait_for(wake.wait(),timeout=2)
                except TimeoutError:pass
    async def deliver():
        last=0.0
        while True:
            # Don't mark sending while disconnected. Durable pending queue survives reconnect.
            if not transport.connected.is_set():await asyncio.sleep(1);continue
            item=store.next_delivery()
            if not item:await asyncio.sleep(.5);continue
            event=store.rows('SELECT kind FROM events WHERE id=?',(item['event_id'],))
            if event and event[0]['kind']=='autonomy' and engine.quiet(time.time()):
                store.delivery_result(item['id'],'cancelled',error='quiet_hours');continue
            await asyncio.sleep(max(0,config['transport']['min_send_interval_seconds']-(time.monotonic()-last)))
            if time.time()>=item['expires']:
                store.delivery_result(item['id'],'expired');continue
            control=store.rows('SELECT * FROM control WHERE scope=?',(item['scope'],))
            if not control or control[0]['paused'] or control[0]['epoch']!=item['epoch']:
                store.delivery_result(item['id'],'cancelled',error='interrupted');continue
            if event and event[0]['kind']=='autonomy':
                latest=store.rows("SELECT MAX(received) AS t FROM events WHERE scope=? AND kind='message'",(item['scope'],))[0]['t']
                if engine.quiet(time.time()) or (latest and time.time()-latest<config['autonomy']['min_idle_minutes']*60):
                    store.delivery_result(item['id'],'cancelled',error='do_not_interrupt');continue
            if not store.prepare_delivery(item['id']):
                store.delivery_result(item['id'],'cancelled',error='delivery_budget_or_window');continue
            try:
                receipt=await transport.send(item['scope'],item['body'])
            except NotConnected:
                store.delivery_result(item['id'],'retry',error='disconnected_before_send')
            except AmbiguousDelivery:
                store.delivery_result(item['id'],'uncertain',error='delivery_ack_unknown')
            except Exception as exc:
                store.delivery_result(item['id'],'failed',error=type(exc).__name__)
            else:
                store.delivery_result(item['id'],'sent',receipt=receipt)
            last=time.monotonic()
    async def interruptions():
        while True:
            current=engine.current_event
            if current and not engine.valid(current) and hasattr(engine.runtime,'interrupt'):
                engine.runtime.interrupt(current['scope'])
            await asyncio.sleep(.25)
    tasks=[asyncio.create_task(transport.run()),asyncio.create_task(worker()),asyncio.create_task(deliver()),asyncio.create_task(interruptions())]
    try:
        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_EXCEPTION)
        for task in done:task.result()
    finally:
        if engine.current_event and hasattr(engine.runtime,'interrupt'):engine.runtime.interrupt(engine.current_event['scope'])
        for task in tasks:task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):await task
