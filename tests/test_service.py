"""Real loopback WebSocket, simulated OneBot client and MockRuntime; no QQ/Codex login."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from websockets.asyncio.client import connect
from qq_character.store import Store
from qq_character.engine import Engine,MockRuntime
from qq_character.service import serve,ProcessLock


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_socket_mock_qq_round_trip_and_persistent_duplicate(self):
        cfg=json.loads((Path(__file__).parents[1]/'config/example.json').read_text(encoding='utf-8'))
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        cfg['transport'].update(port=port,bot_id='900',allowed_private=['100'],min_send_interval_seconds=0)
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,QQ_CHARACTER_ONEBOT_TOKEN='only-for-local-test-1234567890'):
            store=Store(Path(folder)/'state.db');engine=Engine(cfg,store,MockRuntime())
            task=asyncio.create_task(serve(cfg,store,engine))
            try:
                ws=None
                for _ in range(50):
                    try:
                        ws=await connect(f'ws://127.0.0.1:{port}/onebot',additional_headers={'Authorization':'Bearer only-for-local-test-1234567890','X-Self-ID':'900','X-Client-Role':'Universal'})
                        break
                    except OSError:await asyncio.sleep(.02)
                self.assertIsNotNone(ws)
                event={'post_type':'message','message_type':'private','self_id':900,'user_id':100,'message_id':7,'time':int(time.time()),'message':[{'type':'text','data':{'text':'以后短一点'}}]}
                await ws.send(json.dumps(event))
                action=json.loads(await asyncio.wait_for(ws.recv(),timeout=8))
                self.assertEqual(action['action'],'send_private_msg')
                self.assertEqual(action['params']['user_id'],100)
                await ws.send(json.dumps({'echo':action['echo'],'status':'ok','retcode':0,'data':{'message_id':8}}))
                for _ in range(50):
                    rows=store.rows('SELECT status FROM outbox')
                    if rows and rows[0]['status']=='sent':break
                    await asyncio.sleep(.03)
                self.assertEqual(rows[0]['status'],'sent')
                await ws.send(json.dumps(event));await asyncio.sleep(.1)
                self.assertEqual(store.stats()['events'],1)
                self.assertEqual(store.stats()['outbox'],1)
                await ws.close()
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task
                store.close()

    async def test_process_lock_rejects_second_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            with ProcessLock(Path(folder)/'lock'):
                with self.assertRaises(RuntimeError):
                    with ProcessLock(Path(folder)/'lock'):pass


if __name__=='__main__':unittest.main()
