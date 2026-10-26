import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from qq_character.store import Store
from qq_character.engine import Engine,MockRuntime,blank


SCOPE='onebot:900:private:100'


def config():
    return json.loads((Path(__file__).parents[1]/'config/example.json').read_text(encoding='utf-8'))


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite'
        self.store=Store(self.path);self.cfg=config()
        self.now=datetime(2026,10,26,11,30,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        self.clock=lambda:self.now
        self.engine=Engine(self.cfg,self.store,MockRuntime(),clock=self.clock,
                           source_reader=lambda s:{'text':'FIXTURE: 水下机器人帮助观察深海。','url':s['url'],'sha256':'fixture'})
    def tearDown(self):self.store.close();self.temp.cleanup()
    def put(self,id,text,scope=SCOPE,sender='100'):
        return self.store.ingest(id,scope,'message',text,sender,self.now,self.now)
    def test_correction_changes_future_behavior(self):
        self.put('m1','以后短一点');self.engine.step()
        self.assertEqual(self.store.settings(SCOPE,'100')['reply_max_chars'],80)
        self.now+=1;self.put('m2','今天怎么样');self.engine.step()
        self.assertEqual(self.store.rows('SELECT body FROM outbox WHERE event_id=?',('m2',))[0]['body'],'我在，慢慢说。')
        self.assertEqual(self.store.settings(SCOPE,'200')['reply_max_chars'],320)
    def test_restart_remembers_event_without_new_details(self):
        self.put('m1','我们一起给测试文件取名海盐。');self.engine.step();self.store.close()
        self.store=Store(self.path);self.engine.store=self.store;self.now+=2
        self.put('m2','你记得我们给文件取的名字吗');self.engine.step()
        self.assertEqual(self.store.rows('SELECT body FROM outbox WHERE event_id=?',('m2',))[0]['body'],'我们一起给测试文件取名海盐。')
    def test_private_memory_is_not_available_in_group(self):
        self.put('m1','我们一起约定一个私聊秘密。');self.engine.step()
        self.now+=1;self.put('g1','记得私聊的秘密吗','onebot:900:group:500');self.engine.step()
        self.assertIn('没有找到',self.store.rows('SELECT body FROM outbox WHERE event_id=?',('g1',))[0]['body'])
        with self.assertRaises(ValueError):self.store.remember('onebot:900:group:500','100',{'kind':'episodic','key':'leak','content':'秘密','evidence_ids':['m1'],'confidence':1})
    def test_reflection_is_never_its_own_evidence(self):
        self.put('m1','你好');self.engine.step()
        rid=self.store.rows('SELECT id FROM reflections')[0]['id']
        with self.assertRaises(ValueError):self.store.remember(SCOPE,'100',{'kind':'lesson','key':'fake','content':'用户讨厌我','evidence_ids':[rid],'confidence':1})
    def test_autonomy_is_persistent_deduplicated_grounded_and_quiet(self):
        self.cfg['autonomy'].update(enabled=True,scopes=[SCOPE])
        identity=json.dumps(self.cfg['identity'])
        self.assertEqual(self.engine.schedule(self.now),1);self.assertEqual(self.engine.schedule(self.now),0)
        self.engine.step()
        memories=self.store.rows("SELECT * FROM memories WHERE kind='knowledge'")
        self.assertEqual(len(memories),1)
        ids=json.loads(memories[0]['evidence_ids']);self.assertEqual(self.store.evidence(SCOPE,ids)[0]['kind'],'source')
        self.assertEqual(len(self.store.rows('SELECT * FROM outbox')),0)
        self.assertIn('海洋观测方法',self.store.settings(SCOPE,'character')['interests'])
        self.assertEqual(identity,json.dumps(self.cfg['identity']))
        self.store.close();self.store=Store(self.path);self.engine.store=self.store
        self.assertEqual(self.engine.schedule(self.now),0)
    def test_quiet_hours_and_recent_activity_skip_without_model(self):
        self.cfg['autonomy'].update(enabled=True,scopes=[SCOPE])
        self.put('m1','忙着呢');self.engine.step()
        self.engine.schedule(self.now);before=self.store.rows('SELECT COUNT(*) n FROM reservations')[0]['n'];self.engine.step()
        self.assertEqual(before,self.store.rows('SELECT COUNT(*) n FROM reservations')[0]['n'])
        self.now+=12*3600;self.assertTrue(self.engine.quiet(self.now));self.assertEqual(self.engine.schedule(self.now),0)
    def test_duplicate_and_late_events(self):
        self.assertTrue(self.put('same','你好'));self.assertFalse(self.put('same','你好'))
        self.engine.step();self.now-=10;self.put('late','旧消息')
        self.assertEqual(self.store.rows('SELECT status FROM events WHERE id=?',('late',))[0]['status'],'late')
        self.assertFalse(self.engine.step())
    def test_budget_is_durable_and_not_reset_by_deletion(self):
        self.cfg['budgets']['daily_model_calls']=1
        self.put('m1','你好');self.engine.step();self.store.forget(SCOPE,now=self.now)
        self.now+=1;self.put('m2','再来');self.engine.step()
        self.assertEqual(self.store.rows('SELECT status FROM events WHERE id=?',('m2',))[0]['status'],'failed')
    def test_uncertain_delivery_never_automatically_replays(self):
        self.put('m1','你好');self.engine.step();item=self.store.next_delivery(self.now)
        self.assertIsNotNone(item);self.store.recover()
        self.assertEqual(self.store.rows('SELECT status FROM outbox')[0]['status'],'uncertain')
        self.assertIsNone(self.store.next_delivery(self.now+100))
    def test_pause_invalidates_queued_delivery(self):
        self.put('m1','你好');self.engine.step();self.store.pause(SCOPE)
        self.assertIsNone(self.store.next_delivery(self.now))
    def test_no_permission_change_and_no_silence_inference(self):
        self.put('m1','你好')
        for key,value in [('permissions','all'),('reply_max_chars',50),('ask_followups',True)]:
            with self.assertRaises(ValueError):self.store.adapt(SCOPE,'100',{'key':key,'value':value,'evidence_ids':['m1']},self.now)
    def test_forget_removes_context(self):
        self.put('m1','我们一起做了测试');self.engine.step();self.store.forget(SCOPE)
        self.assertTrue(all(not rows for rows in self.store.export(SCOPE).values()))
    def test_reply_expiry(self):
        self.put('m1','你好');self.engine.step();self.assertIsNone(self.store.next_delivery(self.now+1000))
        self.assertEqual(self.store.rows('SELECT status FROM outbox')[0]['status'],'expired')

    def test_stop_prevents_late_result_and_future_messages_still_work(self):
        store=self.store;now=self.now
        class InterruptingRuntime(MockRuntime):
            def run(self,prompt,**kwargs):
                store.ingest('stop',SCOPE,'message','/stop','100',now+1,now+1)
                return super().run(prompt,**kwargs)
        self.engine.runtime=InterruptingRuntime();self.put('m1','以后短一点');self.engine.step()
        self.assertFalse(self.store.rows('SELECT * FROM outbox'))
        self.assertEqual(self.store.settings(SCOPE,'100')['reply_max_chars'],320)
        self.engine.runtime=MockRuntime();self.now+=2;self.put('m2','你好');self.engine.step()
        self.assertEqual(self.store.rows('SELECT event_id FROM outbox')[0]['event_id'],'m2')

    def test_crash_after_queue_does_not_generate_again(self):
        self.put('m1','你好');self.engine.step();self.store.set_event('m1','running')
        self.store.recover();self.assertFalse(self.engine.step())
        self.assertEqual(self.store.stats()['outbox'],1)

    def test_identical_correction_is_idempotent(self):
        self.put('m1','以后短一点')
        candidate={'key':'reply_max_chars','value':80,'evidence_ids':['m1']}
        first=self.store.adapt(SCOPE,'100',candidate,self.now)
        self.assertEqual(first,self.store.adapt(SCOPE,'100',candidate,self.now))

    def test_old_stop_cannot_cancel_newer_task(self):
        self.put('m1','你好');event=self.store.claim(self.now)
        self.store.ingest('old-stop',SCOPE,'message','/stop','100',self.now-10,self.now)
        self.assertTrue(self.engine.valid(event))

    def test_deleted_history_cannot_replay_after_reconnect(self):
        self.put('m1','我们一起测试');self.engine.step();self.store.forget(SCOPE,now=self.now)
        self.assertFalse(self.put('m1','我们一起测试'))
        self.now+=1;self.assertTrue(self.put('m2','新的聊天'))

    def test_stop_between_model_return_and_apply_is_atomic(self):
        parent=self
        class StopBeforeApply(Engine):
            def apply(self,event,output):
                self.store.ingest('stop',SCOPE,'message','/stop','100',parent.now+1,parent.now+1)
                return super().apply(event,output)
        self.engine=StopBeforeApply(self.cfg,self.store,MockRuntime(),clock=self.clock)
        self.put('m1','以后短一点');self.engine.step()
        self.assertEqual(self.store.settings(SCOPE,'100')['reply_max_chars'],320)
        self.assertEqual(self.store.rows("SELECT status FROM events WHERE id='m1'")[0]['status'],'cancelled')
        self.assertFalse(self.store.rows('SELECT * FROM reflections'))

    def test_large_recent_messages_do_not_disable_future_chat(self):
        for n in range(6):
            self.now+=1;self.put('m'+str(n),'很长的测试内容'*1000)
        self.now+=1;self.put('last','你好')
        while self.engine.step():pass
        self.assertNotEqual(self.store.rows("SELECT status FROM events WHERE id='last'")[0]['status'],'failed')

    def test_restart_does_not_catch_up_expired_activity_window(self):
        self.cfg['autonomy'].update(enabled=True,scopes=[SCOPE])
        self.engine.schedule(self.now);self.now+=3600;self.engine.step()
        self.assertEqual(self.store.rows('SELECT status FROM events')[0]['status'],'expired')
        self.assertFalse(self.store.rows('SELECT * FROM reservations'))

    def test_expired_incoming_message_is_not_answered(self):
        self.put('m1','你好');self.now+=600;self.engine.step()
        self.assertEqual(self.store.rows('SELECT status FROM events')[0]['status'],'expired')
        self.assertFalse(self.store.rows('SELECT * FROM outbox'))


if __name__=='__main__':unittest.main()
